"""Seller-owned API providers, with errors safe for hosted streams and logs."""
from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any, Literal, overload


class ProviderUnavailable(RuntimeError):
    def __init__(self):
        super().__init__('The AI provider could not complete the request. Check its connection and try again.')


class HostedProvider:
    # Expose only the shared provider interface, never credentials or clients.
    _operations = frozenset({
        'test_connection', 'list_models', 'list_model_entries', 'analyze_photos',
        'chat', 'structured_chat', 'vision_chat', 'quick_chat', 'web_search',
    })

    def __init__(self, provider):
        self._provider = provider
        self.base_url = getattr(provider, "base_url", "")
        self.name = provider.name
        self.vision_model = provider.vision_model
        self.listing_model = provider.listing_model

    @overload
    def __getattr__(self, name: Literal['test_connection']) -> Callable[..., Awaitable[bool]]: ...

    @overload
    def __getattr__(self, name: Literal['web_search']) -> Callable[[list[dict]], Awaitable[dict]]: ...

    @overload
    def __getattr__(self, name: str) -> Callable[..., Any]: ...

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name not in self._operations:
            raise AttributeError(name)
        operation = getattr(self._provider, name)
        if inspect.isasyncgenfunction(operation):
            async def stream(*args, **kwargs):
                try:
                    async for item in operation(*args, **kwargs):
                        yield item
                except Exception:
                    raise ProviderUnavailable() from None
            return stream

        async def call(*args, **kwargs):
            try:
                return await operation(*args, **kwargs)
            except Exception:
                raise ProviderUnavailable() from None
        return call


def provider_for(choice):
    from vendoo_studio.services.ai_key_catalog import KEY_PROVIDERS
    from vendoo_studio.services.keychain import get_ai_key, get_api_key
    from vendoo_studio.services.listing_provider import key_provider_instance

    if choice in ('chatgpt', 'claude'):
        from vendoo_studio.services.mac_inference import MacSubscriptionProvider, relay_for
        from vendoo_studio.services.service_context import current
        from vendoo_studio.services.user_settings import LISTING_PROVIDER_KEY, read_settings
        context = current()
        if context is None:
            return None
        relay = relay_for(context.seller_id)
        # An explicitly selected Mac must fail clearly when offline, rather
        # than silently charging a different provider.
        selected = read_settings().get(LISTING_PROVIDER_KEY)
        explicitly_selected = isinstance(selected, dict) and selected.get('primary') == choice
        from vendoo_studio.services.device_routes import remote_fresh
        if relay.ready(choice) or remote_fresh(context.seller_id, 'mac') or explicitly_selected:
            return MacSubscriptionProvider(relay, choice)
        return None

    if choice in KEY_PROVIDERS:
        key = get_ai_key(choice)
        return HostedProvider(key_provider_instance(choice, key)) if key else None
    if choice == 'mimo':
        from vendoo_studio.providers.xiaomi_mimo import MiMoProvider
        key = get_api_key()
        return HostedProvider(MiMoProvider(api_key=key)) if key else None
    # SDK sign-ins belong to a registered seller worker. Never inspect the
    # hosted process's HOME or start a CLI on behalf of another account.
    return None
