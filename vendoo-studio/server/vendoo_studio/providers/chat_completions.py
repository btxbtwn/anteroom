"""Any provider that serves OpenAI-compatible Chat Completions on an API key.

Google Gemini, xAI, Mistral and OpenRouter all accept the same request, so one
provider covers them; ``ai_key_catalog`` says where each one lives. No
temperature or token limit is sent: their reasoning models reject or cut
short on values that suit the others, and each provider's default fits its
own models.
"""

from __future__ import annotations

import json
import re

import httpx

from vendoo_studio.providers.photo_analysis import PHOTO_ANALYSIS_SYSTEM
from vendoo_studio.providers.xiaomi_mimo import (
    VISION_MAX_SIDE,
    StreamChunk,
    chunk_text,
    chunk_thinking,
    encode_images,
)
from vendoo_studio.services import live_trace
from vendoo_studio.services.ai_key_catalog import KeyProvider
from vendoo_studio.version import app_version

# OpenRouter shows the calling app on the seller's activity page.
APP_URL = "https://github.com/btxbtwn/list-this-vendoo-suite"
APP_TITLE = "Anteroom"


def _has_images(messages: list[dict]) -> bool:
    return any(
        isinstance(part, dict) and part.get("type") == "image_url"
        for message in messages
        if isinstance(message.get("content"), list)
        for part in message["content"]
    )


def parse_evidence(content: str) -> dict:
    """``{"evidence": {...}}`` from a reply that should be one JSON object."""
    candidates = [content]
    match = re.search(r"\{[\s\S]*\}", content)
    if match:
        candidates.append(match.group())
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return {"evidence": value}
    return {"evidence": {}, "raw": content}


class ChatCompletionsProvider:
    def __init__(self, spec: KeyProvider, api_key: str, vision_model: str, listing_model: str):
        self.spec = spec
        self.name = spec.id
        self.base_url = spec.base_url
        self.api_key = api_key.strip()
        self.vision_model = vision_model
        self.listing_model = listing_model

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": f"Anteroom/{app_version()}",
        }
        if self.spec.id == "openrouter":
            headers["HTTP-Referer"] = APP_URL
            headers["X-Title"] = APP_TITLE
        return headers

    def _http_error(self, resp: httpx.Response) -> str:
        try:
            payload = resp.json()
        except Exception:
            payload = None
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            payload = payload[0]  # Gemini wraps errors in a list
        error = payload.get("error") if isinstance(payload, dict) else None
        detail = error.get("message") if isinstance(error, dict) else error
        if not detail and isinstance(payload, dict):
            detail = payload.get("message") or payload.get("detail")
        text = " ".join(str(detail or resp.text or "").split())[:240]
        label = self.spec.label
        return f"{label} HTTP {resp.status_code}: {text}" if text else f"{label} HTTP {resp.status_code}"

    async def list_models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(f"{self.base_url}/models", headers=self._headers())
        if resp.status_code >= 400:
            raise RuntimeError(self._http_error(resp))
        data = resp.json().get("data") or []
        slugs = {
            str(item["id"]).removeprefix("models/")
            for item in data
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        return sorted(slugs)

    async def test_connection(self) -> bool:
        await self.list_models()
        return True

    async def analyze_photos(
        self,
        photo_paths: list[str],
        notes: str = "",
        listing_rules: str = "",
        max_side: int | None = VISION_MAX_SIDE,
    ) -> dict:
        system = PHOTO_ANALYSIS_SYSTEM
        if notes:
            system += f"\n\nUser notes: {notes}"
        if listing_rules:
            system += f"\n\nAdditional rules:\n{listing_rules}"
        parts: list[dict] = [{"type": "text", "text": "Analyze these product photos:"}]
        for url in await encode_images(photo_paths[:10], max_side):
            parts.append({"type": "image_url", "image_url": {"url": url}})
        messages = [{"role": "system", "content": system}, {"role": "user", "content": parts}]
        try:
            content = await self._complete(messages, self.vision_model)
            return parse_evidence(content)
        except Exception as exc:
            return {"error": str(exc), "evidence": {}}

    async def chat(self, messages: list[dict], stream: bool = True):
        model = self.vision_model if _has_images(messages) else self.listing_model
        if not stream:
            yield await self._complete(messages, model)
            return
        yielded = False
        try:
            async for chunk in self._stream(messages, model):
                yielded = True
                yield chunk
        except Exception:
            if yielded:
                raise
            yield await self._complete(messages, model)
            return
        if not yielded:
            yield await self._complete(messages, model)

    async def vision_chat(self, messages: list[dict]):
        yield await self._complete(messages, self.vision_model)

    async def quick_chat(self, messages: list[dict]):
        yield await self._complete(messages, self.listing_model)

    def _body(self, messages: list[dict], model: str, **extra) -> dict:
        return {"model": model, "messages": messages, **extra}

    async def _post(self, body: dict, timeout: float = 300) -> dict:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{self.base_url}/chat/completions", headers=self._headers(), json=body)
        if resp.status_code >= 400:
            raise RuntimeError(self._http_error(resp))
        return resp.json()

    async def _complete(self, messages: list[dict], model: str) -> str:
        text = chunk_text(await self._post(self._body(messages, model)))
        if not text:
            raise RuntimeError(f"{self.spec.label} returned an empty response")
        return text

    async def _stream(self, messages: list[dict], model: str):
        body = self._body(messages, model, stream=True)
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream(
                "POST", f"{self.base_url}/chat/completions", headers=self._headers(), json=body
            ) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    raise RuntimeError(self._http_error(resp))
                async for line in resp.aiter_lines():
                    data = line[5:].strip() if line.startswith("data:") else line.strip()
                    if not data or data.startswith(":"):
                        continue
                    if data == "[DONE]":
                        break
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    thinking = chunk_thinking(payload)
                    if thinking:
                        yield StreamChunk(thinking, "thinking")
                    text = chunk_text(payload)
                    if text:
                        yield StreamChunk(text, "content")


def openrouter_sources(payload: dict) -> list[dict]:
    """``url_citation`` annotations on the reply, as ``{url, title, description}``.

    OpenRouter nests each citation's fields under ``url_citation``.
    """
    choices = payload.get("choices") or []
    message = choices[0].get("message") if choices and isinstance(choices[0], dict) else None
    annotations = message.get("annotations") if isinstance(message, dict) else None
    sources: list[dict] = []
    seen: set[str] = set()
    for item in annotations if isinstance(annotations, list) else []:
        if not isinstance(item, dict) or item.get("type") != "url_citation":
            continue
        cited = item.get("url_citation") if isinstance(item.get("url_citation"), dict) else item
        url = str(cited.get("url") or "")
        if url.startswith("http") and url not in seen:
            seen.add(url)
            sources.append({
                "url": url,
                "title": str(cited.get("title") or ""),
                "description": str(cited.get("content") or ""),
            })
    return sources


class OpenRouterProvider(ChatCompletionsProvider):
    """OpenRouter adds web search to any model with its ``web`` plugin."""

    async def web_search(self, messages: list[dict]) -> dict:
        payload = await self._post(
            self._body(messages, self.listing_model, plugins=[{"id": "web"}]),
            timeout=180,
        )
        answer = chunk_text(payload).strip()
        sources = openrouter_sources(payload)
        if sources:
            live_trace.emit("step", f"OpenRouter searched the web ({len(sources)} pages)")
        if not answer and not sources:
            raise RuntimeError("OpenRouter web search returned no results")
        return {"answer": answer, "sources": sources}
