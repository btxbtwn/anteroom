"""A fixed-operation inference worker in the Mac app, with no inbound listener."""
from __future__ import annotations

import asyncio
import base64
import inspect
import tempfile
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from vendoo_studio.config import HOSTED_URL
from vendoo_studio.services.mac_inference import OPERATIONS


async def execute(command, provider):
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

    def session(self, token, owner):
        with self._lock:
            self._token = token
            self._owner = owner
            if token and (self._thread is None or not self._thread.is_alive()):
                self._thread = threading.Thread(target=lambda: asyncio.run(self._run()), daemon=True, name='mac-chatgpt-worker')
                self._thread.start()

    async def _run(self):
        from vendoo_studio.services.chatgpt_oauth import chatgpt_signed_in
        from vendoo_studio.providers.chatgpt_codex import ChatGPTCodexProvider
        worker = uuid.uuid4().hex
        active = None
        active_identity = None
        active_owner = None
        async with httpx.AsyncClient(base_url=HOSTED_URL, timeout=30, follow_redirects=False) as client:
            try:
                while True:
                    with self._lock:
                        token = self._token
                        owner = self._owner
                    if active is not None and active_owner != owner:
                        active.cancel()
                        await asyncio.gather(active, return_exceptions=True)
                        active, active_identity = None, None
                    if not token:
                        if active is not None:
                            active.cancel()
                            await asyncio.gather(active, return_exceptions=True)
                            active = None
                            active_identity = None
                        await asyncio.sleep(1)
                        continue
                    if not await asyncio.to_thread(chatgpt_signed_in):
                        await asyncio.sleep(5)
                        continue
                    provider = ChatGPTCodexProvider()
                    headers = {'Authorization': 'Bearer ' + token}
                    if active is not None and active.done():
                        await active
                        active = None
                        active_identity = None
                    try:
                        response = await client.post('/api/mac-ai/next', headers=headers, json={
                            'worker': worker, 'vision_model': provider.vision_model, 'listing_model': provider.listing_model,
                            'available': active is None,
                            'active_call': active_identity,
                        })
                        with self._lock:
                            unchanged = self._token == token
                        if not unchanged:
                            continue
                        if response.status_code == 200:
                            command = response.json()

                            async def answer(command, provider, owner):
                                try:
                                    result = await execute(command, provider)
                                    body = {'result': result, 'failed': False}
                                except Exception:
                                    body = {'result': None, 'failed': True}
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
                            active = asyncio.create_task(answer(command, provider, owner))
                            active_identity = command['id']
                            active_owner = owner
                        elif response.status_code == 204 and active is not None and response.headers.get('X-Anteroom-Call-Active') == '0':
                            active.cancel()
                            await asyncio.gather(active, return_exceptions=True)
                            active, active_identity = None, None
                        elif response.status_code != 204:
                            await asyncio.sleep(5)
                        if active is not None:
                            await asyncio.sleep(2)
                    except httpx.HTTPError:
                        await asyncio.sleep(5)
            finally:
                if active is not None:
                    active.cancel()
                    await asyncio.gather(active, return_exceptions=True)


class MacAIAPI:
    def __init__(self, window):
        self._window = window
        self._worker = MacAIWorker()

    def connect_chatgpt_worker(self, token=None, owner=None):
        window = self._window()
        origin = urlsplit(window.get_current_url() or '') if window is not None else None
        expected = urlsplit(HOSTED_URL)
        if origin is None or (origin.scheme, origin.netloc) != (expected.scheme, expected.netloc):
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
