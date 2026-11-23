"""Any provider that serves OpenAI-compatible Chat Completions on an API key.

Google Gemini, xAI, Mistral and OpenRouter all accept the same request, so one
provider covers them; ``ai_key_catalog`` says where each one lives. No
temperature or token limit is sent: their reasoning models reject or cut
short on values that suit the others, and each provider's default fits its
own models.
"""

from __future__ import annotations

from anteroom.services import model_usage

import asyncio
import json
import re
from dataclasses import dataclass

import httpx
from jsonschema import Draft202012Validator

from anteroom.providers import http_retry
from anteroom.providers.photo_analysis import PHOTO_ANALYSIS_SYSTEM, PHOTO_EVIDENCE_SCHEMA
from anteroom.providers.xiaomi_mimo import (
    VISION_MAX_SIDE,
    StreamChunk,
    chunk_text,
    chunk_thinking,
    encode_images,
)
from anteroom.services import live_trace
from anteroom.services.ai_key_catalog import KeyProvider
from anteroom.services.shared_http import http_client
from anteroom.version import app_version

# OpenRouter shows the calling app on the seller's activity page.
APP_URL = "https://myanteroom.com"
APP_TITLE = "Anteroom"


def _has_images(messages: list[dict]) -> bool:
    return any(
        isinstance(part, dict) and part.get("type") == "image_url"
        for message in messages
        if isinstance(message.get("content"), list)
        for part in message["content"]
    )


_VALUE_FIELDS = ("brand", "size", "color", "material", "style", "graphic", "category", "department")


def _as_text(value):
    """A number the model wrote as a number (a waist of 32, 22.5 inches) is still the read value."""
    return str(value) if isinstance(value, int | float) and not isinstance(value, bool) else value


def _evidence_shape(value: dict) -> dict:
    """Unwrap ``{"evidence": {...}}`` and read numeric values as text before validating."""
    if set(value) == {"evidence"} and isinstance(value["evidence"], dict):
        value = value["evidence"]
    # Optional fields the prompt allows omitting: models often send null instead.
    # Leaving null fails the schema (object only) and aborts the whole read.
    if value.get("slab_label") is None:
        value.pop("slab_label", None)
    for name in _VALUE_FIELDS:
        if isinstance(field := value.get(name), dict) and "value" in field:
            field["value"] = _as_text(field["value"])
    measurements = value.get("measurements")
    for item in measurements if isinstance(measurements, list) else []:
        if isinstance(item, dict) and "value" in item:
            item["value"] = _as_text(item["value"])
    return value


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
            value = _evidence_shape(value)
            if not Draft202012Validator(PHOTO_EVIDENCE_SCHEMA).is_valid(value):
                return {"evidence": {}, "error": "The AI returned incomplete or malformed photo evidence. Retry photo analysis."}
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

    def _http_error(self, resp: httpx.Response) -> http_retry.ProviderHTTPError:
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
        message = f"{label} HTTP {resp.status_code}: {text}" if text else f"{label} HTTP {resp.status_code}"
        return http_retry.ProviderHTTPError(message, resp.status_code)

    async def list_model_entries(self) -> list[ModelEntry]:
        async with http_client() as client:
            resp = await http_retry.send(lambda: client.get(f"{self.base_url}/models", headers=self._headers(), timeout=20))
        if resp.status_code >= 400:
            raise self._http_error(resp)
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
        for url in await encode_images(photo_paths, max_side):
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
        except (http_retry.ProviderHTTPError, httpx.TransportError):
            # Already retried; the same request without streaming would only repeat it.
            raise
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
        # gets the entire budget. Models that always reason may reject
        # reasoning=off with HTTP 400; fall back once to minimal effort when the
        # initial call was not already minimal, and keep both failure reasons.
        try:
            async for chunk in self._stream(messages, model, reasoning=False):
                yield chunk
        except EmptyCompletionError as exc:
            raise RuntimeError(f"{first} Retried with reasoning off and {exc}") from exc
        except http_retry.ProviderHTTPError as exc:
            if exc.status == 400 and self._can_retry_minimal_reasoning(model):
                try:
                    async for chunk in self._stream(messages, model, reasoning={"effort": "minimal"}):
                        yield chunk
                    return
                except EmptyCompletionError as fallback:
                    raise RuntimeError(f"{first} Retried with minimal reasoning and {fallback}") from fallback
                except Exception as fallback:
                    raise RuntimeError(f"{first} Retrying with minimal reasoning failed: {fallback}") from fallback
            raise RuntimeError(f"{first} Retrying with reasoning off failed: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"{first} Retrying with reasoning off failed: {exc}") from exc

    async def structured_chat(self, messages: list[dict], schema: dict):
        payload = await self._post(self._body(messages, self.listing_model,
            response_format={"type": "json_schema", "json_schema": {
                "name": "listing_result", "strict": True, "schema": schema,
            }}))
        if finish_reason(payload) in {"length", "content_filter"}:
            raise RuntimeError(f"{self.spec.label} did not complete the structured response")
        text = chunk_text(payload)
        if not text:
            raise EmptyCompletionError(self.spec.label, self.listing_model, finish_reason(payload))
        yield text

    async def vision_chat(self, messages: list[dict]):
        yield await self._complete(messages, self.vision_model)

    async def quick_chat(self, messages: list[dict]):
        yield await self._complete(messages, self.listing_model)

    def _can_retry_minimal_reasoning(self, model: str) -> bool:
        return not (self.reasoning_effort == "minimal" and model == self.listing_model)

    def _body(self, messages: list[dict], model: str, *, reasoning: bool | dict = True, **extra) -> dict:
        body = {"model": model, "messages": messages, **extra}
        if self.spec.reasoning:
            if isinstance(reasoning, dict):
                body["reasoning"] = reasoning
            elif not reasoning:
                # OpenRouter's switch for a reply that does not think first.
                # Models that always reason may ignore it or reject it with HTTP 400.
                body["reasoning"] = {"enabled": False}
            elif self.reasoning_effort and model == self.listing_model:
                body["reasoning"] = {"effort": self.reasoning_effort}
        return body

    async def _post(self, body: dict, timeout: float = 300, *, endpoint: str = "chat/completions") -> dict:
        with model_usage.observe(self.name, str(body.get("model") or "")) as call:
            async with http_client() as client:
                resp = await http_retry.send(lambda: client.post(
                    f"{self.base_url}/{endpoint}", headers=self._headers(), json=body, timeout=timeout))
            if resp.status_code >= 400:
                raise self._http_error(resp)
            payload = resp.json()
            call.usage(payload.get("usage"), anthropic=False)
            if self.name == "openrouter":
                from anteroom.services import api_usage
                # The ledger insert is a database round trip; keep it off the event loop.
                await asyncio.to_thread(api_usage.note_openrouter_call, call.record, provider_request_id=payload.get("id"))
            return payload

    async def _complete(self, messages: list[dict], model: str, *, reasoning: bool | dict = True) -> str:
        payload = await self._post(self._body(messages, model, reasoning=reasoning))
        text = chunk_text(payload)
        if not text.strip():
            error = EmptyCompletionError(
                self.spec.label, model, finish_reason(payload), len(chunk_thinking(payload)),
            )
            if reasoning is True and self.spec.reasoning and chunk_thinking(payload).strip():
                # Field fills and photo reads use plain completions too. Give
                # reasoning-only replies the same bounded recovery as streams.
                try:
                    return await self._complete(messages, model, reasoning=False)
                except EmptyCompletionError as retry:
                    raise RuntimeError(f"{error} Retried with reasoning off and {retry}") from retry
                except http_retry.ProviderHTTPError as retry:
                    if retry.status == 400 and self._can_retry_minimal_reasoning(model):
                        try:
                            return await self._complete(messages, model, reasoning={"effort": "minimal"})
                        except EmptyCompletionError as fallback:
                            raise RuntimeError(f"{error} Retried with minimal reasoning and {fallback}") from fallback
                        except Exception as fallback:
                            raise RuntimeError(f"{error} Retrying with minimal reasoning failed: {fallback}") from fallback
                    raise RuntimeError(f"{error} Retrying with reasoning off failed: {retry}") from retry
                except Exception as retry:
                    raise RuntimeError(f"{error} Retrying with reasoning off failed: {retry}") from retry
            raise error
        return text

    async def _stream(self, messages: list[dict], model: str, *, reasoning: bool | dict = True):
        with model_usage.observe(self.name, model) as call:
            body = self._body(messages, model, reasoning=reasoning, stream=True, stream_options={"include_usage": True})
            request_id = None
            async with http_client() as client:
                async with http_retry.stream(
                    client, "POST", f"{self.base_url}/chat/completions", headers=self._headers(), json=body, timeout=300,
                ) as resp:
                    if resp.status_code >= 400:
                        await resp.aread()
                        raise self._http_error(resp)
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
                        if request_id is None and payload.get("id"):
                            request_id = payload.get("id")
                        call.usage(payload.get("usage"))
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
            if self.name == "openrouter":
                from anteroom.services import api_usage
                await asyncio.to_thread(api_usage.note_openrouter_call, call.record, provider_request_id=request_id)


def openrouter_sources(payload: dict) -> list[dict]:
    """Citations from OpenRouter Responses output, retaining page excerpts."""
    annotations = [
        annotation
        for item in payload.get("output") or [] if isinstance(item, dict) and item.get("type") == "message"
        for part in item.get("content") or [] if isinstance(part, dict)
        for annotation in part.get("annotations") or []
    ]
    sources: list[dict] = []
    seen: set[str] = set()
    for item in annotations:
        if not isinstance(item, dict) or item.get("type") != "url_citation":
            continue
        cited = item.get("url_citation")
        if not isinstance(cited, dict):
            cited = item
        url = str(cited.get("url") or "")
        if url.startswith("http") and url not in seen:
            seen.add(url)
            sources.append({
                "url": url,
                "title": str(cited.get("title") or ""),
                "description": str(cited.get("content") or ""),
            })
    return sources


# The most web searches one OpenRouter research request may run when the caller
# does not pass a tighter budget, and OpenRouter's own per-request server cap.
WEB_SEARCH_MAX_CALLS = 8
OPENROUTER_WEB_SEARCH_CEILING = 30
# Perplexity's search engine ($5/1K vs $10-$14/1K for native Google/OpenAI
# grounding) returns real page excerpts, verified URLs, and honors
# allowed_domains across every listing model.
OPENROUTER_WEB_SEARCH_ENGINE = "perplexity"


class OpenRouterProvider(ChatCompletionsProvider):
    """OpenRouter runs model-directed web searches through its server tool."""

    def _body(self, messages: list[dict], model: str, *, reasoning: bool | dict = True, **extra) -> dict:
        # One model is served by several hosts, and default routing spreads
        # requests across them at random. A slow host turned a 1,600-token
        # field reply into an 81-second wait, so try the fastest host for the
        # same model first. Fallback to the other hosts stays on.
        # https://openrouter.ai/docs/guides/routing/provider-selection
        provider: dict = {"sort": "throughput"}
        if "response_format" in extra:
            # Hosts that ignore a strict schema would return free text; send
            # the request only to hosts that honour every parameter.
            provider["require_parameters"] = True
        return {**super()._body(messages, model, reasoning=reasoning, **extra), "provider": provider}

    async def web_search(
        self,
        messages: list[dict],
        *,
        max_calls: int | None = None,
        allowed_domains: tuple[str, ...] | list[str] | None = None,
    ) -> dict:
        from anteroom.providers.chatgpt_codex import web_search_answer

        cap = WEB_SEARCH_MAX_CALLS if max_calls is None else max_calls
        if cap < 1:
            raise ValueError("max_calls must be at least 1")
        cap = min(cap, OPENROUTER_WEB_SEARCH_CEILING)
        parameters: dict = {
            "engine": OPENROUTER_WEB_SEARCH_ENGINE,
            "max_uses": cap,
        }
        domains = [d.strip() for d in (allowed_domains or ()) if isinstance(d, str) and d.strip()]
        if domains:
            parameters["allowed_domains"] = domains
        body = self._body(messages, self.listing_model)
        body["input"] = body.pop("messages")
        # Unlike the one-search web plugin, the Responses server tool lets the
        # model refine queries, as the sold-comps research prompt asks.
        # https://openrouter.ai/docs/guides/features/server-tools/web-search
        body["tools"] = [{"type": "openrouter:web_search", "parameters": parameters}]
        # Each search is billed on top of the tokens its results add, and the
        # server tool otherwise allows up to 30 per request.
        body["max_tool_calls"] = cap
        payload = await self._post(body, timeout=90, endpoint="responses")
        answer = web_search_answer(payload.get("output") or [])
        sources = openrouter_sources(payload)
        if sources:
            live_trace.emit("step", f"OpenRouter searched the web ({len(sources)} pages)")
        if not answer and not sources:
            raise RuntimeError("OpenRouter web search returned no results")
        return {"answer": answer, "sources": sources}
