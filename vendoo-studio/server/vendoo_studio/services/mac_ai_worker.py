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


async def subscription_providers():
    from vendoo_studio.services.chatgpt_oauth import chatgpt_signed_in
    from vendoo_studio.services.claude_auth import claude_signed_in
    from vendoo_studio.providers.chatgpt_codex import ChatGPTCodexProvider
    from vendoo_studio.providers.claude_agent import ClaudeProvider

    providers = {}
    if await asyncio.to_thread(chatgpt_signed_in):
        providers['chatgpt'] = ChatGPTCodexProvider()
    if await asyncio.to_thread(claude_signed_in):
        providers['claude'] = ClaudeProvider()
    return providers


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
                self._thread = threading.Thread(target=lambda: asyncio.run(self._run()), daemon=True, name='mac-subscription-worker')
                self._thread.start()

    async def _run(self):
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
                    await cancel([identity for identity, (account, _task) in active.items()
                                  if account != owner or not token])
                    if not token:
                        await asyncio.sleep(1)
                        continue
                    providers = await subscription_providers()
                    models = {name: {'vision_model': provider.vision_model, 'listing_model': provider.listing_model}
                              for name, provider in providers.items()}
                    headers = {'Authorization': 'Bearer ' + token}
                    for identity, (_account, task) in list(active.items()):
                        if task.done():
                            del active[identity]
                            task.result()
                    try:
                        response = await client.post('/api/mac-ai/next', headers=headers, json={
                            'worker': worker, 'providers': models,
                            'available': True,
                        })
                        with self._lock:
                            unchanged = self._owner == owner and self._token is not None
                        if not unchanged:
                            continue
                        if response.status_code == 200:
                            command = response.json()

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
                                except Exception:
                                    body = {'result': None, 'failed': True}
                                finally:
                                    for task in (work, pulse):
                                        task.cancel()
                                    await asyncio.gather(work, pulse, return_exceptions=True)
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
                        elif response.status_code != 204:
                            await asyncio.sleep(5)
                    except httpx.HTTPError:
                        await asyncio.sleep(5)
            finally:
                await cancel(list(active))


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
