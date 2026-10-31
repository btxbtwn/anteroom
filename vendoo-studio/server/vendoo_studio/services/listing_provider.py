from __future__ import annotations

from vendoo_studio.providers.chatgpt_codex import ChatGPTCodexProvider
from vendoo_studio.providers.claude_agent import ClaudeProvider
from vendoo_studio.providers.cursor_agent import CursorProvider
from vendoo_studio.providers.xiaomi_mimo import MiMoProvider
from vendoo_studio.services.ai_key_catalog import KEY_PROVIDER_IDS, KEY_PROVIDERS
from vendoo_studio.services.chatgpt_oauth import chatgpt_signed_in
from vendoo_studio.services.claude_auth import claude_signed_in
from vendoo_studio.services.keychain import get_ai_key, get_ai_keys, get_api_key, get_cursor_api_key
from vendoo_studio.services.user_settings import (
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
    from vendoo_studio.providers.anthropic_api import AnthropicAPIProvider
    from vendoo_studio.providers.chat_completions import ChatCompletionsProvider, OpenRouterProvider
    from vendoo_studio.providers.openai_api import OpenAIAPIProvider

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
    from vendoo_studio import sandbox
    from vendoo_studio.services.service_context import current
    if sandbox.enabled():
        from vendoo_studio.providers.sandbox import SandboxProvider
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


def provider_is_configured() -> bool:
    from vendoo_studio import sandbox
    from vendoo_studio.services.service_context import current
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
