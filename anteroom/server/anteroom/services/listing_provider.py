from __future__ import annotations

from anteroom.providers.chatgpt_codex import ChatGPTCodexProvider
from anteroom.providers.claude_agent import ClaudeProvider
from anteroom.providers.cursor_agent import CursorProvider
from anteroom.providers.xiaomi_mimo import MiMoProvider
from anteroom.services.ai_key_catalog import KEY_PROVIDER_IDS, KEY_PROVIDERS
from anteroom.services.chatgpt_oauth import chatgpt_signed_in
from anteroom.services.claude_auth import claude_signed_in
from anteroom.services.keychain import get_ai_key, get_ai_keys, get_api_key, get_cursor_api_key
from anteroom.services.provider_deadlines import with_deadlines
from anteroom.services.user_settings import (
    get_listing_provider_order,
    get_photo_provider_choice,
    resolved_key_provider_models,
    resolved_key_provider_reasoning,
)


def _mimo_provider():
    key = get_api_key()
    if not key:
        return None
    return MiMoProvider(api_key=key)


def _chatgpt_provider():
    if not chatgpt_signed_in():
        return None
    return ChatGPTCodexProvider()


def _claude_provider():
    if not claude_signed_in():
        return None
    return ClaudeProvider()


def _cursor_provider():
    key = get_cursor_api_key()
    if not key:
        return None
    return CursorProvider(api_key=key)


def key_provider_instance(provider_id: str, api_key: str):
    """The provider for one API-key entry in ``ai_key_catalog``."""
    from anteroom.providers.anthropic_api import AnthropicAPIProvider
    from anteroom.providers.chat_completions import ChatCompletionsProvider, OpenRouterProvider
    from anteroom.providers.openai_api import OpenAIAPIProvider

    spec = KEY_PROVIDERS[provider_id]
    vision, listing = resolved_key_provider_models(provider_id)
    if spec.api == "responses":
        return OpenAIAPIProvider(api_key, vision, listing)
    if spec.api == "anthropic":
        return AnthropicAPIProvider(api_key, vision, listing)
    reasoning = resolved_key_provider_reasoning(provider_id) if spec.reasoning else None
    if spec.id == "openrouter":
        return OpenRouterProvider(spec, api_key, vision, listing, reasoning)
    return ChatCompletionsProvider(spec, api_key, vision, listing, reasoning)


def _key_provider(provider_id: str):
    key = get_ai_key(provider_id)
    if not key:
        return None
    return key_provider_instance(provider_id, key)


def _provider_for(choice: str):
    """The provider for ``choice``, with a deadline on every call it makes."""
    return with_deadlines(_ready_provider(choice))


def _ready_provider(choice: str):
    from anteroom import sandbox
    from anteroom.services.service_context import current
    if sandbox.enabled():
        from anteroom.providers.sandbox import SandboxProvider
        return SandboxProvider()
    context = current()
    if context is not None:
        if context.provider_factory is None:
            raise RuntimeError("Seller execution has no provider factory")
        return context.provider_factory(choice)
    if choice in KEY_PROVIDERS:
        return _key_provider(choice)
    if choice == "mimo":
        return _mimo_provider()
    if choice == "chatgpt":
        return _chatgpt_provider()
    if choice == "claude":
        return _claude_provider()
    if choice == "cursor":
        return _cursor_provider()
    return None


# Tried after the seller's primary and fallback, when neither is ready.
PROVIDER_CHOICES = ("chatgpt", "claude", "cursor", "mimo", *KEY_PROVIDER_IDS)


def get_listing_provider():
    """Return the active listing provider.

    Tries the user's primary choice first, then the configured fallback. When
    neither is ready, any provider the seller has set up is used: adding only a
    Cursor key left the default ChatGPT primary with nothing to run, and every
    listing stopped at "Sign in with ChatGPT" even though a key was saved. A
    fallback of "none" opts out of that.
    """
    primary, fallback = get_listing_provider_order()
    provider = _provider_for(primary)
    if provider is not None:
        return provider
    if fallback == "none":
        return None
    provider = _provider_for(fallback)
    if provider is not None:
        return provider
    for choice in PROVIDER_CHOICES:
        if choice not in (primary, fallback):
            provider = _provider_for(choice)
            if provider is not None:
                return provider
    return None


def get_photo_provider():
    """Return the provider that reads product photos.

    The seller can pick one apart from the listing provider; when that one is
    not ready, photos are read by the listing provider like everything else.
    """
    choice = get_photo_provider_choice()
    if choice != "same":
        provider = _provider_for(choice)
        if provider is not None:
            return provider
    return get_listing_provider()


def managed_task_provider(task: str):
    """Return a task-specific hosted managed provider when active, else None.

    Desktop and seller-BYOK contexts return None so callers keep using the
    seller's configured listing or photo provider unchanged.
    """
    from anteroom import sandbox
    from anteroom.services.hosted_provider import managed_ai_enabled, managed_provider
    from anteroom.services.service_context import current

    if sandbox.enabled() or current() is None or not managed_ai_enabled():
        return None
    if task not in ("field_edit", "measurement"):
        raise ValueError(f"Unsupported managed task: {task}")
    return with_deadlines(managed_provider(task))


def provider_is_configured() -> bool:
    from anteroom import sandbox
    from anteroom.services.service_context import current
    if sandbox.enabled():
        return True
    if current() is not None:
        return any(_provider_for(choice) is not None for choice in PROVIDER_CHOICES)
    return (
        chatgpt_signed_in()
        or bool(get_api_key())
        or bool(get_cursor_api_key())
        or claude_signed_in()
        or bool(get_ai_keys())
    )
