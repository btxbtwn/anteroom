"""A fixed-operation inference worker in the Mac app, with no inbound listener."""
from __future__ import annotations

import asyncio
import base64
import inspect
import re
import tempfile
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from vendoo_studio.config import HOSTED_URL
from vendoo_studio.services.mac_inference import OPERATIONS
from vendoo_studio.services.provider_deadlines import ProviderStalled, with_deadlines

_STATUS = re.compile(r'\bHTTP (\d{3})\b')


async def subscription_providers():
    from vendoo_studio.services.chatgpt_oauth import chatgpt_signed_in
    from vendoo_studio.services.claude_auth import claude_signed_in
    from vendoo_studio.providers.chatgpt_codex import ChatGPTCodexProvider
    from vendoo_studio.providers.claude_agent import ClaudeProvider

    providers = {}
    if await asyncio.to_thread(chatgpt_signed_in):
        providers['chatgpt'] = with_deadlines(ChatGPTCodexProvider())
    if await asyncio.to_thread(claude_signed_in):
        providers['claude'] = with_deadlines(ClaudeProvider())
    return providers


def error_kind(exc):
    """Why a call failed, as one word the server can show; never the provider's text."""
    if isinstance(exc, (ProviderStalled, TimeoutError, httpx.TimeoutException)):
        return 'stalled'
    text = str(exc)
    status = getattr(exc, 'status', None)
    if status is None and (match := _STATUS.search(text)):
        status = int(match.group(1))
    lowered = text.lower()
    if status == 429 or 'rate limit' in lowered or 'usage limit' in lowered:
        return 'rate_limited'
    if status in (401, 403) or 'signed out' in lowered or 'not signed in' in lowered:
        return 'signed_out'
    if isinstance(exc, httpx.TransportError) or (status is not None and status >= 500):
        return 'unavailable'
    if isinstance(exc, ValueError) or (status is not None and 400 <= status < 500):
        return 'rejected'
    return 'failed'


async def execute(command, provider):
    if provider is None:
        raise RuntimeError('Not signed in to this subscription on this Mac')
    operation = command.get('operation')
    if operation not in OPERATIONS:
        raise ValueError('Unsupported inference operation')
    args, kwargs = command.get('args', []), command.get('kwargs', {})
    with tempfile.TemporaryDirectory(prefix='anteroom-inference-') as directory:
        if operation == 'analyze_photos':
            images = args[0]
            if not isinstance(images, list) or len(images) > 24:
                raise ValueError('Invalid photo batch')
            paths = []
            for number, image in enumerate(images):
                if not isinstance(image, str) or not image.startswith('data:image/') or len(image) > 15_000_000:
                    raise ValueError('Invalid photo')
                header, data = image.split(',', 1)
                if ';base64' not in header:
                    raise ValueError('Invalid photo encoding')
                path = Path(directory) / f'{number}.jpg'
                path.write_bytes(base64.b64decode(data, validate=True))
                paths.append(str(path))
            args = [paths]
        result = getattr(provider, operation)(*args, **kwargs)
        if inspect.isasyncgen(result):
            from vendoo_studio.providers.xiaomi_mimo import unpack_stream_item
            chunks, size = [], 0
            async for chunk in result:
                size += len(str(chunk))
                if size > 2_000_000:
                    raise ValueError('Inference response exceeded its limit')
                kind, text = unpack_stream_item(chunk)
                chunks.append({'text': text, 'kind': kind})
            return chunks
        return await result


class MacAIWorker:
    def __init__(self):
        self._token = None
        self._owner = None
        self._lock = threading.Lock()
        self._thread = None
        self._loop = None
        self._task = None
        self._closed = False
        self._paused = False
        self._connection = 'signed_out'
        self._active_calls = 0

    def status(self):
        with self._lock:
            return {'connection': self._connection, 'paused': self._paused, 'active_calls': self._active_calls}

    def set_paused(self, paused):
        with self._lock:
            self._paused = bool(paused)

    def close(self):
        with self._lock:
            self._closed = True
            self._token = self._owner = None
            loop, task, thread = self._loop, self._task, self._thread
        if loop is not None and task is not None and not loop.is_closed():
            loop.call_soon_threadsafe(task.cancel)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=15)

    def session(self, token, owner):
        with self._lock:
            if self._closed:
                return
            if owner != self._owner or not token:
                self._connection = 'connecting' if token else 'signed_out'
            self._token = token
            self._owner = owner
            if token and (self._thread is None or not self._thread.is_alive()):
                self._thread = threading.Thread(target=self._thread_main, daemon=True, name='mac-subscription-worker')
                self._thread.start()

    def _thread_main(self):
        try:
            asyncio.run(self._run())
        except asyncio.CancelledError:
            pass

    async def _run(self):
        with self._lock:
            self._loop, self._task = asyncio.get_running_loop(), asyncio.current_task()
        worker = uuid.uuid4().hex
        active = {}

        async def cancel(identities):
            tasks = [active.pop(identity)[1] for identity in identities]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        async with httpx.AsyncClient(base_url=HOSTED_URL, timeout=30, follow_redirects=False,
                                    limits=httpx.Limits(max_connections=None)) as client:
            try:
                while True:
                    with self._lock:
                        token = self._token
                        owner = self._owner
                        paused, closed = self._paused, self._closed
                    if closed:
                        return
                    await cancel([identity for identity, (account, _task) in active.items()
                                  if account != owner or not token])
                    if not token:
                        await asyncio.sleep(1)
                        continue
                    providers = await subscription_providers()
                    models = {name: {'vision_model': provider.vision_model, 'listing_model': provider.listing_model}
                              for name, provider in providers.items()}
                    if paused:
                        models = {}
                    headers = {'Authorization': 'Bearer ' + token}
                    for identity, (_account, task) in list(active.items()):
                        if task.done():
                            del active[identity]
                            task.result()
                    with self._lock:
                        self._active_calls = len(active)
                    try:
                        response = await client.post('/api/mac-ai/next', headers=headers, json={
                            'worker': worker, 'providers': models,
                            'available': not paused,
                        })
                        with self._lock:
                            unchanged = self._owner == owner and self._token is not None
                        if not unchanged:
                            continue
                        with self._lock:
                            self._connection = ('connected' if response.status_code in (200, 204) else
                                                'in_use' if response.status_code == 409 else
                                                'sign_in_required' if response.status_code in (401, 403) else 'offline')
                        if response.status_code == 200:
                            command = response.json()
                            with self._lock:
                                now_paused = self._paused
                            if now_paused:
                                await client.post('/api/mac-ai/reply', headers=headers, json={
                                    'worker': worker, 'identity': command['id'], 'result': None, 'failed': True,
                                    'error': 'paused',
                                })
                                continue

                            async def answer(command, provider, owner, models):
                                async def watch():
                                    # Each call checks its own cancellation. A long
                                    # poll or another slow call cannot delay Stop.
                                    while True:
                                        await asyncio.sleep(2)
                                        with self._lock:
                                            token = self._token if self._owner == owner else None
                                        if not token:
                                            return
                                        try:
                                            heartbeat = await client.post('/api/mac-ai/next',
                                                headers={'Authorization': 'Bearer ' + token}, json={
                                                    'worker': worker, 'providers': models, 'available': False,
                                                    'active_call': command['id'],
                                                })
                                        except httpx.HTTPError:
                                            continue
                                        if heartbeat.status_code == 204 and heartbeat.headers.get('X-Anteroom-Call-Active') == '0':
                                            return

                                work = asyncio.create_task(execute(command, provider))
                                pulse = asyncio.create_task(watch())
                                try:
                                    done, _pending = await asyncio.wait({work, pulse}, return_when=asyncio.FIRST_COMPLETED)
                                    if pulse in done:
                                        pulse.result()
                                        return
                                    result = work.result()
                                    body = {'result': result, 'failed': False}
                                except Exception as exc:
                                    body = {'result': None, 'failed': True, 'error': error_kind(exc)}
                                finally:
                                    for task in (work, pulse):
                                        task.cancel()
                                    await asyncio.gather(work, pulse, return_exceptions=True)
                                    with self._lock:
                                        self._active_calls = max(0, self._active_calls - 1)
                                with self._lock:
                                    current_token = self._token if self._owner == owner else None
                                if not current_token:
                                    return
                                try:
                                    await client.post('/api/mac-ai/reply', headers={'Authorization': 'Bearer ' + current_token}, json={
                                        'worker': worker, 'identity': command['id'], **body,
                                    })
                                except httpx.HTTPError:
                                    pass
                            active[command['id']] = (owner, asyncio.create_task(answer(command, providers.get(command.get('provider')), owner, models)))
                            with self._lock:
                                self._active_calls = len(active)
                        elif response.status_code != 204:
                            await asyncio.sleep(5)
                        if paused:
                            await asyncio.sleep(1)
                    except httpx.HTTPError:
                        with self._lock:
                            self._connection = 'offline'
                        await asyncio.sleep(5)
            finally:
                await cancel(list(active))
                with self._lock:
                    self._active_calls = 0
                    self._connection = 'signed_out'


class MacAIAPI:
    def __init__(self, window, worker=None):
        self._window = window
        self._worker = worker if worker is not None else MacAIWorker()

    def _trusted_origin(self):
        window = self._window()
        origin = urlsplit(window.get_current_url() or '') if window is not None else None
        expected = urlsplit(HOSTED_URL)
        return origin is not None and (origin.scheme, origin.netloc) == (expected.scheme, expected.netloc)

    def connect_chatgpt_worker(self, token=None, owner=None):
        if not self._trusted_origin():
            return {'ok': False}
        if token is not None and (not isinstance(token, str) or not 0 < len(token) <= 16384):
            return {'ok': False}
        if token is not None:
            try:
                owner = str(uuid.UUID(owner))
            except (ValueError, TypeError, AttributeError):
                return {'ok': False}
        self._worker.session(token, owner)
        return {'ok': True}

    # The account window signs this Mac into ChatGPT through the Mac's own
    # server, which owns the sign-in and its loopback callback. The tokens stay
    # in this Mac's Keychain; the account only learns that the worker is ready.
    _MAC_OFFLINE = 'Anteroom on this Mac did not answer. Quit and reopen Anteroom, then try again.'

    def _local(self, method, path, timeout):
        from vendoo_studio.config import PORT
        return httpx.request(method, f'http://127.0.0.1:{PORT}/api/settings{path}', timeout=timeout)

    def chatgpt_sign_in(self):
        if not self._trusted_origin():
            return {'ok': False}
        try:
            # Sign-in first discovers ChatGPT's endpoints; give that its own deadline.
            response = self._local('POST', '/chatgpt/login', 30)
        except httpx.HTTPError:
            return {'ok': False, 'error': self._MAC_OFFLINE}
        if response.status_code != 200:
            try:
                detail = response.json().get('detail')
            except ValueError:
                detail = None
            return {'ok': False, 'error': detail if isinstance(detail, str) and detail else 'ChatGPT sign-in could not start.'}
        return {'ok': True}

    def chatgpt_sign_in_status(self):
        if not self._trusted_origin():
            return {'ok': False}
        try:
            response = self._local('GET', '/provider', 5)
            chatgpt = response.json().get('chatgpt') if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            chatgpt = None
        if not isinstance(chatgpt, dict):
            return {'ok': False, 'error': self._MAC_OFFLINE}
        error = chatgpt.get('error')
        return {'ok': True, 'signed_in': bool(chatgpt.get('signed_in')), 'pending': bool(chatgpt.get('pending')),
                'error': error if isinstance(error, str) else None}

    def chatgpt_sign_in_cancel(self):
        if not self._trusted_origin():
            return {'ok': False}
        try:
            self._local('DELETE', '/chatgpt/login', 5)
        except httpx.HTTPError:
            return {'ok': False, 'error': self._MAC_OFFLINE}
        return {'ok': True}
