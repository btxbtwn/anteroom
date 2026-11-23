"""Loopback-only hosted app with ephemeral Postgres, signed sessions and storage.

Run through scripts/hosted-sandbox.sh. This entry point is never imported by
production: its local credentials and signing keys exist only for this process.

Load tests (``scripts/hosted-load-test.py``) shape it closer to production with
environment variables, all off by default:

- ``ANTEROOM_SANDBOX_SELLERS``: how many sellers can sign in (``alice``, ``bob``,
  then ``seller3@sandbox.test`` onwards, all with the password ``sandbox``);
- ``ANTEROOM_SANDBOX_DB_RTT_MS``: route the database through a loopback proxy
  that adds this round-trip time, half each way;
- ``ANTEROOM_SANDBOX_WORKER_PROCESS=1``: serve sellers from a ``web`` process
  and run drafts in a separate ``worker`` process, as the Fly groups do.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import jwt
import uvicorn
from jwt.algorithms import ECAlgorithm
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from anteroom.services.seller_sessions import InvalidSession, SessionVerifier

ISSUER = 'https://sandbox.invalid/auth/v1'
# The OAuth client id Supabase would give Claude, for the sandbox's app sign-in.
SANDBOX_APP_ID = 'c1a0de00-5a4d-4b0c-8000-000000000001'
SELLER_NAMES = ('alice', 'bob', *(f'seller{number}' for number in range(3, int(os.environ.get('ANTEROOM_SANDBOX_SELLERS') or 2) + 1)))
ACCOUNTS = {name + '@sandbox.test': str(uuid.uuid5(uuid.NAMESPACE_DNS, 'anteroom-sandbox-' + name))
            for name in SELLER_NAMES}


class HostedSandboxServer(uvicorn.Server):
    """Keep termination graceful until the private database has been cleaned up."""

    @contextmanager
    def capture_signals(self):
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        handlers = {sig: signal.signal(sig, self.handle_exit) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            yield
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)


def auth_error(status: int, code: str, message: str):
    """Supabase Auth's error body, which supabase-js reads the code from."""
    return JSONResponse({'error_code': code, 'msg': message}, status_code=status)


class LocalSupabase:
    """Small protocol stand-in; the real hosted API still verifies signed JWTs."""
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        public = ECAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        public.update(kid='sandbox', alg='ES256', use='sig')
        self.verifier = SessionVerifier('https://sandbox.invalid')
        class LocalKeys(jwt.PyJWKClient):
            def fetch_data(self):
                return {'keys': [public]}
        self.verifier.keys = LocalKeys('https://sandbox.invalid/unused')
        self.server_key = secrets.token_urlsafe(32)
        self.refresh_tokens: dict[str, str] = {}
        # Reset links as codes: the sandbox sends no email, so tests open
        # `/?code=recovery-<name>` the way the real link would.
        self.recovery_codes: dict[str, str] = {}
        self.objects: dict[str, bytes] = {}
        self.types: dict[str, str] = {}
        # Signed links, by token: (key, expiry). Supabase signs them; the sandbox remembers them.
        self.signed: dict[str, tuple[str, float]] = {}
        self.app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        self.app.add_api_route('/auth/v1/token', self.token, methods=['POST'])
        self.app.add_api_route('/auth/v1/user', self.user, methods=['GET'])
        self.app.add_api_route('/auth/v1/user', self.update_user, methods=['PUT'])
        self.app.add_api_route('/auth/v1/signup', self.signup, methods=['POST'])
        self.app.add_api_route('/auth/v1/recover', self.recover, methods=['POST'])
        self.app.add_api_route('/auth/v1/logout', self.logout, methods=['POST'])
        # Supabase's OAuth 2.1 server, for apps such as Claude signing in to /api/mcp.
        # A pending request per sandbox app: tests open `/oauth/consent?authorization_id=sandbox-<app>`.
        self.authorizations: dict[str, dict[str, str]] = {
            'sandbox-claude': {'client_id': SANDBOX_APP_ID, 'name': 'Claude',
                               'redirect_uri': 'https://claude.invalid/oauth/callback', 'state': 'sandbox'},
        }
        self.oauth_codes: dict[str, tuple[str, str]] = {}
        self.grants: set[tuple[str, str]] = set()
        self.app.add_api_route('/auth/v1/oauth/authorizations/{authorization_id}', self.authorization, methods=['GET'])
        self.app.add_api_route('/auth/v1/oauth/authorizations/{authorization_id}/consent', self.consent, methods=['POST'])
        self.app.add_api_route('/auth/v1/oauth/token', self.oauth_token, methods=['POST'])
        self.app.add_api_route('/auth/v1/user/oauth/grants', self.revoke_grant, methods=['DELETE'])
        self.app.add_api_route('/storage/v1/object/anteroom-photos/{key:path}', self.storage, methods=['GET', 'POST', 'PUT'])
        self.app.add_api_route('/storage/v1/object/anteroom-photos', self.delete, methods=['DELETE'])
        self.app.add_api_route('/storage/v1/object/sign/anteroom-photos/{key:path}', self.sign, methods=['POST'])
        self.app.add_api_route('/storage/v1/object/sign/anteroom-photos/{key:path}', self.signed_read, methods=['GET'])

    def session(self, email: str):
        now = int(time.time())
        claims = {'sub': ACCOUNTS[email], 'email': email, 'aud': 'authenticated', 'role': 'authenticated',
                  'iss': ISSUER, 'iat': now, 'exp': now + 3600, 'session_id': str(uuid.uuid4()), 'is_anonymous': False}
        token = jwt.encode(claims, self.key, algorithm='ES256', headers={'kid': 'sandbox'})
        refresh = secrets.token_urlsafe(32)
        self.refresh_tokens[refresh] = email
        return {'access_token': token, 'refresh_token': refresh, 'token_type': 'bearer', 'expires_in': 3600,
                'expires_at': now + 3600, 'user': self.user_body(email)}

    @staticmethod
    def user_body(email: str):
        return {'id': ACCOUNTS[email], 'email': email, 'aud': 'authenticated', 'role': 'authenticated',
                'app_metadata': {'provider': 'email', 'providers': ['email']}, 'user_metadata': {},
                'identities': [], 'created_at': '2026-01-01T00:00:00Z', 'updated_at': '2026-01-01T00:00:00Z'}

    async def token(self, request: Request):
        body = await request.json()
        if request.query_params.get('grant_type') == 'refresh_token':
            email = self.refresh_tokens.pop(body.get('refresh_token'), None)
        elif request.query_params.get('grant_type') == 'pkce':
            email = self.recovery_codes.pop(body.get('auth_code'), None)
        else:
            email = body.get('email') if body.get('password') == 'sandbox' else None
        if email not in ACCOUNTS:
            return auth_error(400, 'invalid_credentials', 'Use alice@sandbox.test or bob@sandbox.test with password sandbox')
        return self.session(email)

    async def signup(self, request: Request):
        # New accounts confirm their email first, so Supabase answers without a session.
        email = (await request.json()).get('email', '')
        if email in ACCOUNTS:
            return auth_error(422, 'user_already_exists', 'User already registered')
        return {'id': str(uuid.uuid4()), 'email': email, 'aud': 'authenticated', 'role': 'authenticated',
                'identities': [{'provider': 'email'}], 'confirmation_sent_at': '2026-01-01T00:00:00Z'}

    async def recover(self, request: Request):
        email = (await request.json()).get('email', '')
        if email in ACCOUNTS:
            self.recovery_codes['recovery-' + email.split('@')[0]] = email
        return {}

    async def update_user(self, request: Request):
        owner = self.owner(request)
        if len((await request.json()).get('password', '')) < 8:
            return auth_error(422, 'weak_password', 'Password should be at least 8 characters.')
        return self.user_body(next(email for email, user_id in ACCOUNTS.items() if user_id == owner))

    def owner(self, request: Request):
        try:
            return self.verifier.verify(request.headers.get('authorization', '').removeprefix('Bearer ')).user_id
        except InvalidSession:
            raise HTTPException(401, 'A local sandbox session is required') from None

    def user(self, request: Request):
        owner = self.owner(request)
        return self.user_body(next(email for email, user_id in ACCOUNTS.items() if user_id == owner))

    def logout(self, request: Request):
        owner = self.owner(request)
        self.refresh_tokens = {token: email for token, email in self.refresh_tokens.items() if ACCOUNTS[email] != owner}
        return Response(status_code=204)

    def authorization(self, authorization_id: str, request: Request):
        owner = self.owner(request)
        pending = self.authorizations.get(authorization_id)
        if pending is None:
            return auth_error(404, 'oauth_authorization_not_found', 'Authorization not found')
        email = next(email for email, user_id in ACCOUNTS.items() if user_id == owner)
        return {'authorization_id': authorization_id, 'redirect_uri': pending['redirect_uri'],
                'client': {'id': pending['client_id'], 'name': pending['name'], 'uri': '', 'logo_uri': ''},
                'user': {'id': owner, 'email': email}, 'scope': 'email'}

    async def consent(self, authorization_id: str, request: Request):
        owner = self.owner(request)
        pending = self.authorizations.get(authorization_id)
        if pending is None:
            return auth_error(404, 'oauth_authorization_not_found', 'Authorization not found')
        if (await request.json()).get('action') != 'approve':
            return {'redirect_url': f"{pending['redirect_uri']}?error=access_denied&state={pending['state']}"}
        code = secrets.token_urlsafe(24)
        self.oauth_codes[code] = (owner, pending['client_id'])
        self.grants.add((owner, pending['client_id']))
        return {'redirect_url': f"{pending['redirect_uri']}?code={code}&state={pending['state']}"}

    async def oauth_token(self, request: Request):
        """The app trades its code for a token that carries its ``client_id``."""
        form = await request.form()
        owner, client_id = self.oauth_codes.pop(str(form.get('code', '')), ('', ''))
        if not owner:
            return auth_error(400, 'invalid_grant', 'Invalid or used authorization code')
        now = int(time.time())
        claims = {'sub': owner, 'aud': 'authenticated', 'role': 'authenticated', 'iss': ISSUER, 'iat': now,
                  'exp': now + 3600, 'session_id': str(uuid.uuid4()), 'is_anonymous': False, 'client_id': client_id}
        token = jwt.encode(claims, self.key, algorithm='ES256', headers={'kid': 'sandbox'})
        return {'access_token': token, 'token_type': 'bearer', 'expires_in': 3600}

    def revoke_grant(self, request: Request, client_id: str = ''):
        owner = self.owner(request)
        self.grants.discard((owner, client_id))
        return Response(status_code=204)

    def check_storage_key(self, key: str, request: Request):
        server_key = request.headers.get('apikey', '')
        owner = key.split('/')[0] if secrets.compare_digest(server_key, self.server_key) else self.owner(request)
        if owner not in ACCOUNTS.values() or not key.startswith(owner + '/') or '..' in key.split('/'):
            raise HTTPException(404, 'Photo not found')

    async def storage(self, key: str, request: Request):
        self.check_storage_key(key, request)
        if request.method == 'GET':
            if key not in self.objects:
                raise HTTPException(404, 'Photo not found')
            return Response(self.objects[key], media_type=self.types.get(key, 'image/jpeg'))
        # The bucket's limit: eBay's 150 MB, for listing videos.
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 157_286_400:
                raise HTTPException(413, 'Sandbox object is too large')
        self.objects[key] = bytes(data)
        self.types[key] = request.headers.get('content-type', 'image/jpeg').split(';')[0]
        return {'Key': key}

    async def sign(self, key: str, request: Request):
        self.check_storage_key(key, request)
        if key not in self.objects:
            raise HTTPException(404, 'Object not found')
        seconds = int((await request.json()).get('expiresIn') or 60)
        token = secrets.token_urlsafe(24)
        self.signed[token] = (key, time.time() + seconds)
        return {'signedURL': f'/object/sign/anteroom-photos/{key}?token={token}'}

    def signed_read(self, key: str, token: str = ''):
        signed_key, expires = self.signed.get(token, ('', 0.0))
        if signed_key != key or expires < time.time() or key not in self.objects:
            raise HTTPException(400, 'Invalid signature')
        return Response(self.objects[key], media_type=self.types.get(key, 'application/octet-stream'),
                        headers={'Access-Control-Allow-Origin': '*'})

    async def delete(self, request: Request):
        for key in (await request.json()).get('prefixes', []):
            self.check_storage_key(key, request)
            self.objects.pop(key, None)
            self.types.pop(key, None)
        return []


class DelayedDatabase:
    """Loopback TCP proxy to the sandbox Postgres that delays every chunk each way.

    Production's database is about 24 ms away (Fly San Jose to Supabase
    Oregon); the sandbox's is a local socket. Each direction delivers a chunk
    ``one_way`` seconds after it arrived, in order, so a round trip costs about
    twice that without limiting throughput.
    """

    def __init__(self, socket_path: str, one_way: float):
        self.socket_path, self.one_way = socket_path, one_way
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.port = 0
        self.thread = threading.Thread(target=self.run, name='anteroom-db-delay', daemon=True)
        self.thread.start()
        if not self.ready.wait(10):
            raise RuntimeError('The database delay proxy did not start')

    def run(self):
        asyncio.set_event_loop(self.loop)
        server = self.loop.run_until_complete(asyncio.start_server(self.connect, '127.0.0.1', 0))
        self.port = server.sockets[0].getsockname()[1]
        self.ready.set()
        try:
            self.loop.run_forever()
        finally:
            server.close()
            self.loop.close()

    def stop(self):
        async def close():
            tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.loop.stop()
        asyncio.run_coroutine_threadsafe(close(), self.loop)
        self.thread.join(10)

    async def connect(self, client_reader, client_writer):
        try:
            server_reader, server_writer = await asyncio.open_unix_connection(self.socket_path)
        except OSError:
            client_writer.close()
            return
        try:
            await asyncio.gather(self.pipe(client_reader, server_writer), self.pipe(server_reader, client_writer),
                                 return_exceptions=True)
        finally:
            # stop() cancels this task; close both ends either way.
            for writer in (client_writer, server_writer):
                writer.close()

    async def pipe(self, reader, writer):
        chunks: asyncio.Queue[tuple[float, bytes]] = asyncio.Queue()

        async def deliver():
            while True:
                due, data = await chunks.get()
                await asyncio.sleep(max(0.0, due - self.loop.time()))
                if not data:
                    writer.close()
                    return
                writer.write(data)
                await writer.drain()

        delivering = asyncio.ensure_future(deliver())
        try:
            while data := await reader.read(65536):
                chunks.put_nowait((self.loop.time() + self.one_way, data))
        except OSError:
            pass
        chunks.put_nowait((self.loop.time() + self.one_way, b''))
        await asyncio.gather(delivering, return_exceptions=True)


def database_url(postgres) -> tuple[str, DelayedDatabase | None]:
    """The sandbox database's URL, through the delay proxy when one is asked for."""
    rtt_ms = float(os.environ.get('ANTEROOM_SANDBOX_DB_RTT_MS') or 0)
    if rtt_ms <= 0:
        return postgres.get_uri(), None
    info = postgres.get_postmaster_info()
    proxy = DelayedDatabase(f'{info.socket_dir}/.s.PGSQL.{info.port}', rtt_ms / 2000)
    return f'postgresql://postgres:@127.0.0.1:{proxy.port}/postgres', proxy


def start_worker_process(root: Path, port: int) -> subprocess.Popen | None:
    """Run drafts in a second process, as Fly's worker group does, when asked to."""
    if os.environ.get('ANTEROOM_SANDBOX_WORKER_PROCESS') != '1':
        return None
    os.environ['ANTEROOM_PROCESS_ROLE'] = 'web'
    return subprocess.Popen([sys.executable, '-m', 'anteroom.hosted_sandbox', '--worker'], env={
        **os.environ, 'ANTEROOM_PROCESS_ROLE': 'worker', 'ANTEROOM_DATA_DIR': str(root / 'worker-data'),
        'ANTEROOM_PORT': str(port + 2),
    })


def stop_worker_process(worker: subprocess.Popen | None):
    if worker is None:
        return
    worker.terminate()
    try:
        worker.wait(30)
    except subprocess.TimeoutExpired:
        worker.kill()
        worker.wait()


def serve_worker(port: int):
    """The worker half of a split sandbox: drafts and schedulers, no sellers."""
    from anteroom import sandbox
    if os.environ.get('ANTEROOM_SANDBOX') != '1' or os.environ.get('ANTEROOM_PROCESS_ROLE') != 'worker':
        raise SystemExit('The sandbox worker starts only from a split hosted sandbox')
    sandbox.install()
    from anteroom.hosted import create_app
    # It verifies no seller sessions; the web process does, against LocalSupabase.
    uvicorn.run(create_app('https://sandbox.invalid'), host='127.0.0.1', port=port, access_log=False, log_level='warning')


async def serve(port: int):
    from pgserver.postgres_server import get_server
    from anteroom import sandbox

    outer = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    # The phone and browsers talk to this server directly, not through Fly's
    # proxy. Uvicorn's default 5-second keep-alive closes a connection the
    # client is about to reuse, and a write sent on it fails as "network
    # connection lost" (it is never resent). Hold idle connections longer.
    server = HostedSandboxServer(uvicorn.Config(outer, host='127.0.0.1', port=port, timeout_keep_alive=75))
    # Keep handlers active while starting and cleaning up the private database.
    with server.capture_signals():
        # Ignore inherited production URLs: this command owns a new database and
        # never migrates, seeds or connects to any operator-supplied database.
        with tempfile.TemporaryDirectory(prefix='anteroom-hosted-sandbox-') as directory:
            root = Path(directory)
            postgres = get_server(root / 'postgres', cleanup_mode='delete')
            database_engine = None
            proxy = worker = None
            try:
                if server.should_exit:
                    return
                url, proxy = database_url(postgres)
                os.environ.update(ANTEROOM_DATABASE_URL=url, ANTEROOM_DATA_DIR=str(root / 'data'),
                                  ANTEROOM_SANDBOX='1', ANTEROOM_BACKGROUND_WORKER='1',
                                  SUPABASE_URL=f'http://127.0.0.1:{port}/local', SUPABASE_PUBLISHABLE_KEY='sandbox-public')
                sandbox.install()
                from anteroom.database import engine, init_db
                database_engine = engine
                from anteroom.services.category_tree_seed import import_seed
                init_db()
                import_seed(engine)
                local = LocalSupabase()
                os.environ["SUPABASE_SERVER_SECRET_KEY"] = local.server_key
                worker = start_worker_process(root, port)
                from anteroom.hosted import create_app
                hosted = create_app('https://sandbox.invalid', session_verifier=local.verifier)
                web_port = int(os.environ.get('ANTEROOM_WEB_PORT', '5175'))
                outer.add_middleware(CORSMiddleware, allow_origins=[f'http://127.0.0.1:{web_port}'],
                                     allow_methods=['GET', 'POST', 'PUT', 'DELETE'], allow_headers=['*'])
                @outer.middleware('http')
                async def refuse_live_actions(request: Request, call_next):
                    reason = sandbox.refusal(request.method, request.url.path)
                    if reason:
                        return JSONResponse({'detail': reason}, status_code=403)
                    return await call_next(request)

                @outer.get('/api/health')
                def health():
                    from anteroom.version import app_version
                    return {'status': 'ok', 'service': 'anteroom-hosted', 'version': app_version(), 'sandbox': True}

                outer.mount('/local', local.app)
                outer.mount('/', hosted)
                # Mounted applications do not receive lifespan events automatically.
                async with hosted.router.lifespan_context(hosted):

                    async def seed_when_ready():
                        from anteroom.sandbox_seed import seed_hosted
                        while not server.started:
                            await asyncio.sleep(.05)
                        await asyncio.to_thread(seed_hosted, f'http://127.0.0.1:{port}')
                        print(f'Hosted sandbox ready: http://127.0.0.1:{port}', flush=True)
                        # For scripts/hosted-load-test.py's --database-url and --pid.
                        print(f'Sandbox database: {postgres.get_uri()}; API process {os.getpid()}'
                              + (f', worker process {worker.pid}' if worker else ''), flush=True)

                    seed_task = asyncio.create_task(seed_when_ready())
                    serving = asyncio.create_task(server.serve())
                    try:
                        done, _ = await asyncio.wait({serving, seed_task}, return_when=asyncio.FIRST_COMPLETED)
                        if seed_task in done:
                            seed_task.result()
                        await serving
                    finally:
                        server.should_exit = True
                        seed_task.cancel()
                        await asyncio.gather(seed_task, return_exceptions=True)
                        await serving
            finally:
                try:
                    stop_worker_process(worker)
                    if database_engine is not None:
                        database_engine.dispose()
                finally:
                    if proxy is not None:
                        proxy.stop()
                    postgres.cleanup()


def main():
    if os.environ.get('FLY_APP_NAME'):
        raise SystemExit('The local sandbox must not run on hosted infrastructure')
    port = int(os.environ.get('ANTEROOM_PORT', '4320'))
    if port == 4318:
        raise SystemExit('The hosted sandbox cannot use the real desktop port 4318')
    if sys.argv[1:] == ['--worker']:
        serve_worker(port)
        return
    asyncio.run(serve(port))


if __name__ == '__main__':
    main()
