"""Account-scoped, transient inference calls to an outbound-connected Mac.

Draft persistence remains in the durable draft worker. Like an upstream model
request, an inference call fails if its connection disappears; it is not replayed.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
import uuid
from collections import deque

OPERATIONS = frozenset({'analyze_photos', 'chat', 'vision_chat', 'structured_chat', 'quick_chat', 'web_search'})
SUBSCRIPTIONS = frozenset({'chatgpt', 'claude'})


class MacUnavailable(RuntimeError):
    def __init__(self):
        super().__init__('Open Anteroom on your Mac, sign into this account, and connect the selected subscription there. Keep the Mac awake and online, then retry.')


class MacInference:
    def __init__(self, owner=None):
        self.owner = owner
        self.condition = threading.Condition()
        self.worker = None
        self.seen = 0.0
        self.models = {}
        self.outbox = deque()
        self.pending = {}

    def _local(self):
        with self.condition:
            return self.worker is not None and time.monotonic() - self.seen < 45

    @property
    def connected(self):
        if self._local():
            return True
        from vendoo_studio.services.device_routes import remote_fresh
        return remote_fresh(self.owner, 'mac')

    def ready(self, provider):
        return self._local() and provider in self.models

    def poll(self, worker, models, available=True, timeout=20):
        with self.condition:
            if self.worker != worker and self.connected:
                raise MacUnavailable()
            if self.worker != worker:
                for future in self.pending.values():
                    if not future.done():
                        try:
                            future.set_exception(MacUnavailable())
                        except concurrent.futures.InvalidStateError:
                            pass
                self.pending.clear()
                self.outbox.clear()
            self.worker, self.models, self.seen = worker, models, time.monotonic()
            if available:
                self.condition.wait_for(lambda: bool(self.outbox) or self.worker != worker, timeout)
            if self.worker != worker:
                return None
            self.seen = time.monotonic()
            while available and self.outbox:
                command = self.outbox.popleft()
                if command['id'] in self.pending:
                    return command
            return None

    def reply(self, worker, identity, result, failed):
        with self.condition:
            if worker != self.worker:
                return False
            future = self.pending.get(identity)
            if future is None or future.done():
                return False
            try:
                if failed:
                    future.set_exception(MacUnavailable())
                else:
                    future.set_result(result)
            except concurrent.futures.InvalidStateError:
                return False
            return True

    async def invoke(self, operation, args, kwargs, provider):
        if provider not in SUBSCRIPTIONS or operation not in OPERATIONS:
            raise ValueError('Unsupported subscription operation')
        from vendoo_studio.services.device_routes import RoutingUnavailable, forward_target, post
        try:
            target = await forward_target(self.owner, 'mac')
        except RoutingUnavailable:
            raise MacUnavailable from None
        if target is not None:
            try:
                payload = await post(target, '/internal/mac-inference/invoke', {
                    'user_id': self.owner, 'provider': provider, 'operation': operation, 'args': args, 'kwargs': kwargs,
                }, timeout=310)
            except RoutingUnavailable:
                raise MacUnavailable from None
            if not payload.get('ok'):
                raise MacUnavailable
            return payload.get('result')
        identity = uuid.uuid4().hex
        future = concurrent.futures.Future()
        with self.condition:
            # No other process holds this Mac, so only a local one can take it.
            if not self.ready(provider):
                raise MacUnavailable()
            self.pending[identity] = future
            self.outbox.append({'id': identity, 'provider': provider, 'operation': operation, 'args': args, 'kwargs': kwargs})
            self.condition.notify_all()
        try:
            return await asyncio.wait_for(asyncio.wrap_future(future), 300)
        except TimeoutError:
            raise MacUnavailable() from None
        finally:
            with self.condition:
                self.pending.pop(identity, None)
                self.outbox = deque(command for command in self.outbox if command['id'] != identity)


_relays = {}
_lock = threading.Lock()


def relay_for(owner):
    with _lock:
        relay = _relays.get(owner)
        if relay is None:
            relay = MacInference(owner)
            _relays[owner] = relay
        return relay


class MacSubscriptionProvider:
    def __init__(self, relay, provider):
        self.relay = relay
        self.name = provider
        self.base_url = 'https://api.openai.com/v1' if provider == 'chatgpt' else 'https://api.anthropic.com'
        models = relay.models.get(provider, {})
        self.vision_model = models.get('vision_model', '')
        self.listing_model = models.get('listing_model', '')

    async def test_connection(self):
        # Exercise the selected subscription, rather than only its heartbeat.
        await self.relay.invoke('chat', [[{'role': 'user', 'content': 'Reply OK.'}]], {'stream': False}, self.name)
        return True

    async def analyze_photos(self, photo_paths, **kwargs):
        from vendoo_studio.providers.xiaomi_mimo import encode_images
        images = await encode_images(photo_paths, kwargs.pop('max_side', 1600))
        return await self.relay.invoke('analyze_photos', [images], kwargs, self.name)

    async def chat(self, messages, stream=True):
        async for chunk in self._stream('chat', [messages], {'stream': stream}):
            yield chunk

    async def vision_chat(self, messages):
        async for chunk in self._stream('vision_chat', [messages], {}):
            yield chunk

    async def structured_chat(self, messages, schema):
        async for chunk in self._stream('structured_chat', [messages, schema], {}):
            yield chunk

    async def quick_chat(self, messages):
        # Claude's local provider implements quick requests through chat,
        # matching listing_generate's handling of this optional capability.
        operation, kwargs = ('chat', {'stream': False}) if self.name == 'claude' else ('quick_chat', {})
        async for chunk in self._stream(operation, [messages], kwargs):
            yield chunk

    async def _stream(self, operation, args, kwargs):
        from vendoo_studio.providers.xiaomi_mimo import StreamChunk
        result = await self.relay.invoke(operation, args, kwargs, self.name)
        if not isinstance(result, list):
            raise MacUnavailable()
        for item in result:
            if not isinstance(item, dict) or not isinstance(item.get('text'), str) or not isinstance(item.get('kind'), str):
                raise MacUnavailable()
            yield StreamChunk(item['text'], item['kind'])

    async def web_search(self, messages):
        return await self.relay.invoke('web_search', [messages], {}, self.name)
