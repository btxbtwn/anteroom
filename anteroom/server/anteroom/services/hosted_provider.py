"""Seller-owned API providers, with errors safe for hosted streams and logs."""
from __future__ import annotations

import inspect
import os
from collections.abc import Awaitable, Callable
from typing import Any, Literal, overload

DEFAULT_MANAGED_MODEL = 'google/gemini-3.5-flash-lite'
DEFAULT_MANAGED_REASONING = 'minimal'
DEFAULT_MANAGED_FIELD_EDIT_MODEL = 'anthropic/claude-haiku-5.5'
DEFAULT_MANAGED_MEASUREMENT_MODEL = 'openai/gpt-6.2-sol'
ManagedTask = Literal['default', 'field_edit', 'measurement']
_MANAGED_REASONING_EFFORTS = frozenset({'minimal', 'low', 'medium', 'high', 'xhigh', 'max'})
_MANAGED_REASONING_OFF = frozenset({'none', 'off'})


def managed_openrouter_key() -> str:
    """Platform OpenRouter key for hosted accounts; empty when seller-supplied providers are used."""
    return os.environ.get('ANTEROOM_MANAGED_OPENROUTER_KEY', '').strip()


def managed_model() -> str:
    """Pinned OpenRouter model slug for photos, listings, size tags, assistant and sold comps."""
    return os.environ.get('ANTEROOM_MANAGED_MODEL', '').strip() or DEFAULT_MANAGED_MODEL


def managed_field_edit_model() -> str:
    """Pinned OpenRouter model slug for chat edits and automatic marketplace field fills."""
    return os.environ.get('ANTEROOM_MANAGED_FIELD_EDIT_MODEL', '').strip() or DEFAULT_MANAGED_FIELD_EDIT_MODEL


def managed_measurement_model() -> str:
    """Pinned OpenRouter model slug for yardstick measurement reading."""
    return os.environ.get('ANTEROOM_MANAGED_MEASUREMENT_MODEL', '').strip() or DEFAULT_MANAGED_MEASUREMENT_MODEL


def managed_reasoning() -> str | None:
    """OpenRouter reasoning effort for the default managed model (defaults to minimal)."""
    value = os.environ.get('ANTEROOM_MANAGED_REASONING', '').strip().lower()
    if not value:
        return DEFAULT_MANAGED_REASONING
    if value in _MANAGED_REASONING_OFF:
        return None
    return value if value in _MANAGED_REASONING_EFFORTS else None


def managed_ai_enabled() -> bool:
    """Whether hosted accounts automatically use Anteroom's managed OpenRouter models."""
    if managed_openrouter_key():
        return True
    from anteroom import sandbox
    return sandbox.enabled() and os.environ.get('ANTEROOM_MANAGED_AI', '').strip().lower() in {'1', 'true', 'yes'}


def managed_provider(task: ManagedTask = 'default') -> HostedProvider | None:
    key = managed_openrouter_key()
    if not key:
        return None
    from anteroom.providers.chat_completions import OpenRouterProvider
    from anteroom.services.ai_key_catalog import KEY_PROVIDERS

    if task == 'field_edit':
        model, reasoning = managed_field_edit_model(), None
    elif task == 'measurement':
        model, reasoning = managed_measurement_model(), None
    else:
        model, reasoning = managed_model(), managed_reasoning()
    return HostedProvider(
        OpenRouterProvider(KEY_PROVIDERS['openrouter'], key, model, model, reasoning),
    )


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
    def __getattr__(self, name: Literal['web_search']) -> Callable[..., Awaitable[dict]]: ...

    @overload
    def __getattr__(self, name: str) -> Callable[..., Any]: ...

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name not in self._operations:
            raise AttributeError(name)
        operation = getattr(self._provider, name)
        # Copy only __signature__ (never functools.wraps) so callers can inspect
        # optional kwargs without exposing self._provider via __wrapped__.__self__.
        try:
            sig = inspect.signature(operation)
        except (TypeError, ValueError):
            sig = None
        if inspect.isasyncgenfunction(operation):
            async def stream(*args, **kwargs):
                try:
                    async for item in operation(*args, **kwargs):
                        yield item
                except Exception:
                    raise ProviderUnavailable() from None
            if sig is not None:
                stream.__signature__ = sig  # type: ignore[attr-defined]
            return stream

        async def call(*args, **kwargs):
            try:
                return await operation(*args, **kwargs)
            except Exception:
                raise ProviderUnavailable() from None
        if sig is not None:
            call.__signature__ = sig  # type: ignore[attr-defined]
        return call


def provider_for(choice):
    if managed_ai_enabled():
        return managed_provider()
    from anteroom.services.ai_key_catalog import KEY_PROVIDERS
    from anteroom.services.keychain import get_ai_key, get_api_key
    from anteroom.services.listing_provider import key_provider_instance

    if choice in ('chatgpt', 'claude'):
        from anteroom.services.mac_inference import MacSubscriptionProvider, existing_relay
        from anteroom.services.service_context import current
        from anteroom.services.user_settings import LISTING_PROVIDER_KEY, read_settings
        context = current()
        if context is None:
            return None
        relay = existing_relay(context.seller_id)
        # An explicitly selected Mac must fail clearly when offline, rather
        # than silently charging a different provider.
        selected = read_settings().get(LISTING_PROVIDER_KEY)
        explicitly_selected = isinstance(selected, dict) and selected.get('primary') == choice
        from anteroom.services.device_routes import remote_fresh
        if (relay is not None and relay.ready(choice)) or remote_fresh(context.seller_id, 'mac') or explicitly_selected:
            return MacSubscriptionProvider(relay, choice, owner=context.seller_id)
        return None

    if choice in KEY_PROVIDERS:
        key = get_ai_key(choice)
        return HostedProvider(key_provider_instance(choice, key)) if key else None
    if choice == 'mimo':
        from anteroom.providers.xiaomi_mimo import MiMoProvider
        key = get_api_key()
        return HostedProvider(MiMoProvider(api_key=key)) if key else None
    # SDK sign-ins belong to a registered seller worker. Never inspect the
    # hosted process's HOME or start a CLI on behalf of another account.
    return None
