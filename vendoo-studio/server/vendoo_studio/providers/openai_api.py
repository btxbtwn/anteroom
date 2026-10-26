"""OpenAI's Responses API on the seller's own API key.

The ChatGPT sign-in speaks the same Responses format, so this reuses that
provider and changes only where requests go and how they are signed.
"""

from __future__ import annotations

import httpx

from vendoo_studio.providers.chatgpt_codex import DEFAULT_REASONING_EFFORT, ChatGPTCodexProvider, _http_client
from vendoo_studio.services.ai_key_catalog import key_provider

SPEC = key_provider("openai")


def openai_http_error(resp: httpx.Response) -> str:
    try:
        payload = resp.json()
    except Exception:
        payload = None
    error = payload.get("error") if isinstance(payload, dict) else None
    detail = error.get("message") if isinstance(error, dict) else None
    text = " ".join(str(detail or resp.text or "").split())[:240]
    return f"OpenAI HTTP {resp.status_code}: {text}" if text else f"OpenAI HTTP {resp.status_code}"


class OpenAIAPIProvider(ChatGPTCodexProvider):
    name = "openai"
    base_url = SPEC.base_url

    def __init__(self, api_key: str, vision_model: str, listing_model: str):
        self.api_key = api_key.strip()
        self.vision_model = vision_model
        self.listing_model = listing_model
        self.reasoning_effort = DEFAULT_REASONING_EFFORT

    async def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

    def _http_error(self, resp: httpx.Response) -> str:
        return openai_http_error(resp)

    def _payload(self, messages: list[dict], model: str, stream: bool, **kwargs) -> dict:
        payload = super()._payload(messages, model, stream, **kwargs)
        # Reasoning summaries need a verified OpenAI organization, which a
        # seller's new key usually is not; asking for one fails the request.
        payload.get("reasoning", {}).pop("summary", None)
        return payload

    async def list_models(self) -> list[str]:
        async with _http_client() as client:
            resp = await client.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=20,
            )
        if resp.status_code >= 400:
            raise RuntimeError(openai_http_error(resp))
        data = resp.json().get("data") or []
        return sorted(
            str(item["id"])
            for item in data
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        )

    async def test_connection(self) -> bool:
        await self.list_models()
        return True
