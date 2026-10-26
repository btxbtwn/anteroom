"""AI providers the seller connects with their own API key.

Each entry is everything Studio needs to talk to one provider: which wire
format it speaks, where, and the models it starts with. The seller can pick
other models from the provider's own list; these are only the defaults,
checked against each provider's model docs in October 2026.

``api`` is the wire format:

- ``responses``: OpenAI's Responses API, the format the ChatGPT provider uses.
- ``anthropic``: Anthropic's Messages API.
- ``chat``: OpenAI-compatible Chat Completions, which Google, xAI, Mistral and
  OpenRouter all serve.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class KeyProvider:
    id: str
    label: str
    api: Literal["responses", "anthropic", "chat"]
    base_url: str
    vision_model: str
    listing_model: str
    key_url: str
    key_hint: str
    # The provider searches the web itself, so sold comps need no Brave key.
    web_search: bool = False
    # The provider offers its own sign-in that hands Studio a key.
    sign_in: bool = False


KEY_PROVIDERS: dict[str, KeyProvider] = {
    provider.id: provider
    for provider in (
        KeyProvider(
            id="openai",
            label="OpenAI",
            api="responses",
            base_url="https://api.openai.com/v1",
            vision_model="gpt-6.1-sol",
            listing_model="gpt-6.1-sol",
            key_url="https://platform.openai.com/api-keys",
            key_hint="sk-...",
            web_search=True,
        ),
        KeyProvider(
            id="anthropic",
            label="Anthropic",
            api="anthropic",
            base_url="https://api.anthropic.com/v1",
            vision_model="claude-sonnet-5-5",
            listing_model="claude-sonnet-5-5",
            key_url="https://console.anthropic.com/settings/keys",
            key_hint="sk-ant-...",
            web_search=True,
        ),
        KeyProvider(
            id="gemini",
            label="Google Gemini",
            api="chat",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            vision_model="gemini-3.8-flash",
            listing_model="gemini-3.8-flash",
            key_url="https://aistudio.google.com/apikey",
            key_hint="AIza...",
        ),
        KeyProvider(
            id="xai",
            label="xAI Grok",
            api="chat",
            base_url="https://api.x.ai/v1",
            vision_model="grok-4.7",
            listing_model="grok-4.7",
            key_url="https://console.x.ai",
            key_hint="xai-...",
        ),
        KeyProvider(
            id="mistral",
            label="Mistral",
            api="chat",
            base_url="https://api.mistral.ai/v1",
            vision_model="mistral-small-2603",
            listing_model="mistral-large-4",
            key_url="https://console.mistral.ai/api-keys",
            key_hint="",
        ),
        KeyProvider(
            id="openrouter",
            label="OpenRouter",
            api="chat",
            base_url="https://openrouter.ai/api/v1",
            # OpenRouter's router picks a model that can handle each request.
            vision_model="openrouter/auto",
            listing_model="openrouter/auto",
            key_url="https://openrouter.ai/settings/keys",
            key_hint="sk-or-...",
            web_search=True,
            sign_in=True,
        ),
    )
}

KEY_PROVIDER_IDS: tuple[str, ...] = tuple(KEY_PROVIDERS)


def key_provider(provider_id: str) -> KeyProvider:
    try:
        return KEY_PROVIDERS[provider_id]
    except KeyError:
        raise ValueError(f"Unknown AI provider: {provider_id}") from None
