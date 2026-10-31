"""Loopback-only hosted app with ephemeral Postgres, signed sessions and storage.

Run through scripts/hosted-sandbox.sh. This entry point is never imported by
production: its local credentials and signing keys exist only for this process.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import tempfile
import time
import uuid
from pathlib import Path

import jwt
from jwt.algorithms import ECAlgorithm
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from vendoo_studio.services.seller_sessions import InvalidSession, SessionVerifier

ISSUER = 'https://sandbox.invalid/auth/v1'
ACCOUNTS = {name + '@sandbox.test': str(uuid.uuid5(uuid.NAMESPACE_DNS, 'anteroom-sandbox-' + name))
            for name in ('alice', 'bob')}


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
        self.objects: dict[str, bytes] = {}
        self.app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        self.app.add_api_route('/auth/v1/token', self.token, methods=['POST'])
        self.app.add_api_route('/auth/v1/user', self.user, methods=['GET'])
        self.app.add_api_route('/auth/v1/logout', self.logout, methods=['POST'])
        self.app.add_api_route('/storage/v1/object/anteroom-photos/{key:path}', self.storage, methods=['GET', 'POST', 'PUT'])
        self.app.add_api_route('/storage/v1/object/anteroom-photos', self.delete, methods=['DELETE'])

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
        else:
            email = body.get('email') if body.get('password') == 'sandbox' else None
        if email not in ACCOUNTS:
            raise HTTPException(400, 'Use alice@sandbox.test or bob@sandbox.test with password sandbox')
        return self.session(email)

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
            return Response(self.objects[key], media_type='image/jpeg')
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 20 * 1024 * 1024:
                raise HTTPException(413, 'Sandbox photo is too large')
        self.objects[key] = bytes(data)
        return {'Key': key}

    async def delete(self, request: Request):
        for key in (await request.json()).get('prefixes', []):
            self.check_storage_key(key, request)
            self.objects.pop(key, None)
        return []


async def serve(port: int):
    from pgserver.postgres_server import get_server
    import uvicorn
    from vendoo_studio import sandbox

    # Ignore inherited production URLs: this command owns a new database and
    # never migrates, seeds or connects to any operator-supplied database.
    with tempfile.TemporaryDirectory(prefix='anteroom-hosted-sandbox-') as directory:
        root = Path(directory)
        postgres = get_server(root / 'postgres', cleanup_mode='delete')
        os.environ.update(ANTEROOM_DATABASE_URL=postgres.get_uri(), VENDOO_STUDIO_DATA_DIR=str(root / 'data'),
                          ANTEROOM_SANDBOX='1', ANTEROOM_BACKGROUND_WORKER='1',
                          SUPABASE_URL=f'http://127.0.0.1:{port}/local', SUPABASE_PUBLISHABLE_KEY='sandbox-public')
        try:
            sandbox.install()
            from vendoo_studio.database import engine, init_db
            from vendoo_studio.services.category_tree_seed import import_seed
            init_db()
            import_seed(engine)
            local = LocalSupabase()
            os.environ["SUPABASE_SERVER_SECRET_KEY"] = local.server_key
            from vendoo_studio.hosted import create_app
            hosted = create_app('https://sandbox.invalid', session_verifier=local.verifier)
            outer = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
            web_port = int(os.environ.get('VENDOO_STUDIO_WEB_PORT', '5175'))
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
                from vendoo_studio.version import app_version
                return {'status': 'ok', 'service': 'anteroom-hosted', 'version': app_version(), 'sandbox': True}

            outer.mount('/local', local.app)
            outer.mount('/', hosted)
            # Mounted applications do not receive lifespan events automatically.
            async with hosted.router.lifespan_context(hosted):
                server = uvicorn.Server(uvicorn.Config(outer, host='127.0.0.1', port=port))

                async def seed_when_ready():
                    from vendoo_studio.sandbox_seed import seed_hosted
                    while not server.started:
                        await asyncio.sleep(.05)
                    await asyncio.to_thread(seed_hosted, f'http://127.0.0.1:{port}')
                    print(f'Hosted sandbox ready: http://127.0.0.1:{port}', flush=True)

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
            postgres.cleanup()


def main():
    if os.environ.get('FLY_APP_NAME'):
        raise SystemExit('The local sandbox must not run on hosted infrastructure')
    port = int(os.environ.get('VENDOO_STUDIO_PORT', '4320'))
    if port == 4318:
        raise SystemExit('The hosted sandbox cannot use the real desktop port 4318')
    asyncio.run(serve(port))


if __name__ == '__main__':
    main()
