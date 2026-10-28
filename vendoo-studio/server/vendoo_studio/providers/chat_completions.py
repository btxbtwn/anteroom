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
from dataclasses import dataclass

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


class EmptyCompletionError(RuntimeError):
    """A model finished a reply without any visible text.

    Reasoning models sometimes spend the whole reply thinking and stop before
    writing the answer, which arrives as an empty reply rather than an HTTP
    error. Naming what came back makes that retryable instead of mystifying.
    """

    def __init__(self, label: str, model: str, finish: str = "", reasoning_chars: int = 0):
        if reasoning_chars:
            detail = f"streamed {reasoning_chars:,} characters of reasoning but no answer"
        else:
            detail = "returned no answer"
        if finish:
            detail += f" (finish: {finish})"
        super().__init__(f"{label} ({model}) {detail}.")


def finish_reason(payload: dict) -> str:
    """The reply's ``finish_reason``, when the provider sent one."""
    choices = payload.get("choices") or []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    value = choice.get("finish_reason")
    return value if isinstance(value, str) and value else ""


@dataclass(frozen=True)
class ModelEntry:
    """One model in a provider's catalog, with what its picker needs."""

    slug: str
    name: str
    reasoning_efforts: tuple[str, ...] = ()


class ChatCompletionsProvider:
    def __init__(
        self,
        spec: KeyProvider,
        api_key: str,
        vision_model: str,
        listing_model: str,
        reasoning_effort: str | None = None,
    ):
        self.spec = spec
        self.name = spec.id
        self.base_url = spec.base_url
        self.api_key = api_key.strip()
        self.vision_model = vision_model
        self.listing_model = listing_model
        self.reasoning_effort = reasoning_effort

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

    async def list_model_entries(self) -> list[ModelEntry]:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(f"{self.base_url}/models", headers=self._headers())
        if resp.status_code >= 400:
            raise RuntimeError(self._http_error(resp))
        data = resp.json().get("data") or []
        entries: dict[str, ModelEntry] = {}
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            slug = item["id"].removeprefix("models/")
            name = item.get("name")
            entries[slug] = ModelEntry(
                slug=slug,
                name=name.strip() if isinstance(name, str) and name.strip() else slug,
                reasoning_efforts=self._reasoning_efforts(item),
            )
        return [entries[slug] for slug in sorted(entries)]

    def _reasoning_efforts(self, item: dict) -> tuple[str, ...]:
        """The model's advertised efforts, when it takes OpenRouter's reasoning knob."""
        if not self.spec.reasoning:
            return ()
        params = item.get("supported_parameters")
        if not isinstance(params, list) or not {"reasoning", "reasoning_effort"} & set(params):
            return ()
        reasoning = item.get("reasoning")
        efforts = reasoning.get("supported_efforts") if isinstance(reasoning, dict) else None
        if isinstance(efforts, list):
            values = tuple(str(effort).strip() for effort in efforts if str(effort).strip())
            if values:
                return values
        # OpenRouter accepts these for any reasoning-capable model; a few models
        # advertise no list of their own.
        return ("low", "medium", "high")

    async def list_models(self) -> list[str]:
        return [entry.slug for entry in await self.list_model_entries()]

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
            return
        except EmptyCompletionError as exc:
            first = exc
        except Exception:
            if yielded:
                raise
            yield await self._complete(messages, model)
            return
        if not yielded:
            # Nothing arrived over the stream; one plain request may still work.
            try:
                yield await self._complete(messages, model)
                return
            except EmptyCompletionError as exc:
                raise RuntimeError(f"{first} {exc}") from exc
        # A reasoning model can spend the whole reply thinking and stop before
        # writing the listing. Ask again with reasoning off so the visible answer
        # gets the entire budget. A model that always reasons ignores the flag, so
        # the second try can fail the same way; both reasons are then reported.
        try:
            async for chunk in self._stream(messages, model, reasoning=False):
                yield chunk
        except EmptyCompletionError as exc:
            raise RuntimeError(f"{first} Retried with reasoning off and {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"{first} Retrying with reasoning off failed: {exc}") from exc

    async def vision_chat(self, messages: list[dict]):
        yield await self._complete(messages, self.vision_model)

    async def quick_chat(self, messages: list[dict]):
        yield await self._complete(messages, self.listing_model)

    def _body(self, messages: list[dict], model: str, *, reasoning: bool = True, **extra) -> dict:
        body = {"model": model, "messages": messages, **extra}
        if self.spec.reasoning and not reasoning:
            # OpenRouter's switch for a reply that does not think first. Models
            # that always reason ignore it.
            body["reasoning"] = {"enabled": False}
        elif self.reasoning_effort and model == self.listing_model:
            body["reasoning"] = {"effort": self.reasoning_effort}
        return body

    async def _post(self, body: dict, timeout: float = 300) -> dict:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{self.base_url}/chat/completions", headers=self._headers(), json=body)
        if resp.status_code >= 400:
            raise RuntimeError(self._http_error(resp))
        return resp.json()

    async def _complete(self, messages: list[dict], model: str) -> str:
        payload = await self._post(self._body(messages, model))
        text = chunk_text(payload)
        if not text.strip():
            raise EmptyCompletionError(
                self.spec.label, model, finish_reason(payload), len(chunk_thinking(payload)),
            )
        return text

    async def _stream(self, messages: list[dict], model: str, *, reasoning: bool = True):
        body = self._body(messages, model, reasoning=reasoning, stream=True)
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream(
                "POST", f"{self.base_url}/chat/completions", headers=self._headers(), json=body
            ) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    raise RuntimeError(self._http_error(resp))
                content_chars = 0
                reasoning_chars = 0
                finish = ""
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
                    finish = finish_reason(payload) or finish
                    thinking = chunk_thinking(payload)
                    if thinking:
                        reasoning_chars += len(thinking)
                        yield StreamChunk(thinking, "thinking")
                    text = chunk_text(payload)
                    if text:
                        content_chars += len(text.strip())
                        yield StreamChunk(text, "content")
                if not content_chars:
                    raise EmptyCompletionError(self.spec.label, model, finish, reasoning_chars)


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
