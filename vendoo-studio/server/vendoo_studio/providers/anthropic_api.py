"""Anthropic's Messages API on the seller's own API key.

Unlike the Claude sign-in, this needs no Claude Code install: requests go
straight to api.anthropic.com.
"""

from __future__ import annotations

from vendoo_studio.services import model_usage

import json
import re

import httpx

from vendoo_studio.providers import http_retry
from vendoo_studio.providers.chat_completions import parse_evidence
from vendoo_studio.providers.claude_agent import _image_block
from vendoo_studio.providers.photo_analysis import PHOTO_ANALYSIS_SYSTEM
from vendoo_studio.providers.xiaomi_mimo import VISION_MAX_SIDE, StreamChunk, encode_images
from vendoo_studio.services import live_trace
from vendoo_studio.services.ai_key_catalog import key_provider
from vendoo_studio.services.shared_http import http_client

SPEC = key_provider("anthropic")
API_VERSION = "2023-06-01"
MAX_TOKENS = 16_000
MAX_SEARCH_RESUMES = 3
# Web search with dynamic filtering (it trims pages before they reach the
# model) needs Opus or Sonnet 4.6 or later, or a Fable or Mythos model; older
# models and every Haiku keep the basic tool.
# https://docs.claude.com/en/docs/agents-and-tools/tool-use/web-search-tool
WEB_SEARCH_TYPE = "web_search_20260209"
BASIC_WEB_SEARCH_TYPE = "web_search_20250305"
_MODEL_VERSION = re.compile(r"^claude-(opus|sonnet|haiku|fable|mythos)-(\d+)(?:-(\d{1,2}))?(?:$|-)")


def web_search_tool(model: str) -> dict:
    """The newest web search tool ``model`` takes."""
    match = _MODEL_VERSION.match(model)
    family, major, minor = (match.group(1), int(match.group(2)), int(match.group(3) or 0)) if match else ("", 0, 0)
    current = family in {"fable", "mythos"} or (family in {"opus", "sonnet"} and (major, minor) >= (4, 6))
    return {"type": WEB_SEARCH_TYPE if current else BASIC_WEB_SEARCH_TYPE, "name": "web_search", "max_uses": 5}


# An error status or a lost connection was already retried; sending the same
# request again without streaming would only repeat it.
NO_FALLBACK = (http_retry.ProviderHTTPError, httpx.TransportError)


def to_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """Studio's chat history as Anthropic's ``system`` and ``messages``."""
    system: list[str] = []
    turns: list[dict] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content")
        if role == "system":
            if isinstance(content, str) and content.strip():
                system.append(content)
            continue
        blocks: list[dict] = []
        if isinstance(content, str):
            if content.strip():
                blocks.append({"type": "text", "text": content})
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, str) and part.strip():
                    blocks.append({"type": "text", "text": part})
                elif isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
                    blocks.append({"type": "text", "text": str(part["text"])})
                elif isinstance(part, dict) and part.get("type") == "image_url" and role == "user":
                    image = part.get("image_url")
                    url = image.get("url") if isinstance(image, dict) else image
                    block = _image_block(str(url or ""))
                    if block is not None:
                        blocks.append(block)
        if blocks:
            turns.append({"role": "assistant" if role in {"assistant", "model"} else "user", "content": blocks})
    if not turns or turns[0]["role"] != "user":
        turns.insert(0, {"role": "user", "content": [{"type": "text", "text": "Continue."}]})
    return "\n\n".join(system), turns


def reply_text(payload: dict) -> str:
    return "".join(
        str(block.get("text") or "")
        for block in payload.get("content") or []
        if isinstance(block, dict) and block.get("type") == "text"
    )


def reply_sources(payload: dict) -> list[dict]:
    """Pages the web search tool read and cited, as ``{url, title, description}``."""
    sources: list[dict] = []
    seen: set[str] = set()

    def add(url: object, title: object, description: object = "") -> None:
        href = str(url or "").strip()
        if href.startswith("http") and href not in seen:
            seen.add(href)
            sources.append({"url": href, "title": str(title or ""), "description": str(description or "")})

    for block in payload.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            for citation in block.get("citations") or []:
                if isinstance(citation, dict):
                    add(citation.get("url"), citation.get("title"), citation.get("cited_text"))
        elif block.get("type") == "web_search_tool_result":
            results = block.get("content")
            for result in results if isinstance(results, list) else []:
                if isinstance(result, dict):
                    add(result.get("url"), result.get("title"))
    return sources


class AnthropicAPIProvider:
    name = "anthropic"
    base_url = SPEC.base_url

    def __init__(self, api_key: str, vision_model: str, listing_model: str):
        self.api_key = api_key.strip()
        self.vision_model = vision_model
        self.listing_model = listing_model

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }

    def _http_error(self, resp: httpx.Response) -> http_retry.ProviderHTTPError:
        try:
            payload = resp.json()
        except Exception:
            payload = None
        error = payload.get("error") if isinstance(payload, dict) else None
        detail = error.get("message") if isinstance(error, dict) else None
        text = " ".join(str(detail or resp.text or "").split())[:240]
        message = f"Anthropic HTTP {resp.status_code}: {text}" if text else f"Anthropic HTTP {resp.status_code}"
        return http_retry.ProviderHTTPError(message, resp.status_code)

    def _body(self, messages: list[dict], model: str, **extra) -> dict:
        system, turns = to_messages(messages)
        # Automatic caching: Anthropic caches up to the last cacheable block, so
        # the long system prompt and earlier turns are read from cache on the
        # next call with the same prefix (field fills, repairs, chat).
        # https://docs.claude.com/en/docs/build-with-claude/prompt-caching
        body: dict = {"model": model, "max_tokens": MAX_TOKENS, "messages": turns,
                      "cache_control": {"type": "ephemeral"}, **extra}
        if system:
            body["system"] = system
        return body

    async def list_models(self) -> list[str]:
        async with http_client() as client:
            resp = await http_retry.send(lambda: client.get(
                f"{self.base_url}/models", headers=self._headers(), params={"limit": 1000}, timeout=20))
        if resp.status_code >= 400:
            raise self._http_error(resp)
        data = resp.json().get("data") or []
        return [str(item["id"]) for item in data if isinstance(item, dict) and isinstance(item.get("id"), str)]

    async def test_connection(self) -> bool:
        await self.list_models()
        return True

    async def _post(self, body: dict, timeout: float = 300) -> dict:
        with model_usage.observe(self.name, str(body.get("model") or "")) as call:
            async with http_client() as client:
                resp = await http_retry.send(lambda: client.post(
                    f"{self.base_url}/messages", headers=self._headers(), json=body, timeout=timeout))
            if resp.status_code >= 400:
                raise self._http_error(resp)
            payload = resp.json()
            call.usage(payload.get("usage"), anthropic=True)
            return payload

    async def _complete(self, messages: list[dict], model: str) -> str:
        text = reply_text(await self._post(self._body(messages, model)))
        if not text:
            raise RuntimeError("Anthropic returned an empty response")
        return text

    async def _stream(self, messages: list[dict], model: str):
        with model_usage.observe(self.name, model) as call:
            async with http_client() as client:
                async with http_retry.stream(
                    client,
                    "POST",
                    f"{self.base_url}/messages",
                    headers=self._headers(),
                    json=self._body(messages, model, stream=True),
                    timeout=300,
                ) as resp:
                    if resp.status_code >= 400:
                        await resp.aread()
                        raise self._http_error(resp)
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            event = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue
                        message = event.get("message") or {}
                        call.usage(message.get("usage") or event.get("usage"), anthropic=True)
                        if event.get("type") == "error":
                            error = event.get("error") or {}
                            raise RuntimeError(f"Anthropic: {error.get('message') or error}")
                        if event.get("type") != "content_block_delta":
                            continue
                        delta = event.get("delta") or {}
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            yield StreamChunk(str(delta["text"]), "content")
                        elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                            yield StreamChunk(str(delta["thinking"]), "thinking")

    async def structured_chat(self, messages: list[dict], schema: dict):
        payload = await self._post(self._body(messages, self.listing_model,
            output_config={"format": {"type": "json_schema", "schema": schema}}))
        if payload.get("stop_reason") in {"max_tokens", "refusal"}:
            raise RuntimeError("Anthropic did not complete the structured response")
        text = reply_text(payload)
        if not text:
            raise RuntimeError("Anthropic returned no structured response")
        yield text

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
            return parse_evidence(await self._complete(messages, self.vision_model))
        except Exception as exc:
            return {"error": str(exc), "evidence": {}}

    async def chat(self, messages: list[dict], stream: bool = True):
        if not stream:
            yield await self._complete(messages, self.listing_model)
            return
        yielded = False
        try:
            async for chunk in self._stream(messages, self.listing_model):
                yielded = True
                yield chunk
        except NO_FALLBACK:
            raise
        except Exception:
            if yielded:
                raise
            # The stream broke before answering, without an error status: one
            # plain request may still work.
            yield await self._complete(messages, self.listing_model)
            return
        if not yielded:
            yield await self._complete(messages, self.listing_model)

    async def vision_chat(self, messages: list[dict]):
        yield await self._complete(messages, self.vision_model)

    async def quick_chat(self, messages: list[dict]):
        yield await self._complete(messages, self.listing_model)

    async def web_search(self, messages: list[dict]) -> dict:
        body = self._body(messages, self.listing_model, tools=[web_search_tool(self.listing_model)])
        payload = await self._post(body, timeout=180)
        blocks = list(payload.get("content") or [])
        # A long search can pause mid-turn; sending the partial reply back resumes it.
        for _ in range(MAX_SEARCH_RESUMES):
            if payload.get("stop_reason") != "pause_turn":
                break
            body["messages"] = [*body["messages"], {"role": "assistant", "content": payload.get("content") or []}]
            payload = await self._post(body, timeout=180)
            blocks.extend(payload.get("content") or [])
        reply = {"content": blocks}
        sources = reply_sources(reply)
        if sources:
            live_trace.emit("step", f"Anthropic searched the web ({len(sources)} pages)")
        answer = reply_text(reply).strip()
        if not answer and not sources:
            raise RuntimeError("Anthropic web search returned no results")
        return {"answer": answer, "sources": sources}
