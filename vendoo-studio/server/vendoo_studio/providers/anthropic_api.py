"""Anthropic's Messages API on the seller's own API key.

Unlike the Claude sign-in, this needs no Claude Code install: requests go
straight to api.anthropic.com.
"""

from __future__ import annotations

import json

import httpx

from vendoo_studio.providers.chat_completions import parse_evidence
from vendoo_studio.providers.claude_agent import _image_block
from vendoo_studio.providers.photo_analysis import PHOTO_ANALYSIS_SYSTEM
from vendoo_studio.providers.xiaomi_mimo import VISION_MAX_SIDE, StreamChunk, encode_images
from vendoo_studio.services import live_trace
from vendoo_studio.services.ai_key_catalog import key_provider

SPEC = key_provider("anthropic")
API_VERSION = "2023-06-01"
MAX_TOKENS = 16_000
MAX_SEARCH_RESUMES = 3
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}


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

    def _http_error(self, resp: httpx.Response) -> str:
        try:
            payload = resp.json()
        except Exception:
            payload = None
        error = payload.get("error") if isinstance(payload, dict) else None
        detail = error.get("message") if isinstance(error, dict) else None
        text = " ".join(str(detail or resp.text or "").split())[:240]
        return f"Anthropic HTTP {resp.status_code}: {text}" if text else f"Anthropic HTTP {resp.status_code}"

    def _body(self, messages: list[dict], model: str, **extra) -> dict:
        system, turns = to_messages(messages)
        body: dict = {"model": model, "max_tokens": MAX_TOKENS, "messages": turns, **extra}
        if system:
            body["system"] = system
        return body

    async def list_models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(f"{self.base_url}/models", headers=self._headers(), params={"limit": 1000})
        if resp.status_code >= 400:
            raise RuntimeError(self._http_error(resp))
        data = resp.json().get("data") or []
        return [str(item["id"]) for item in data if isinstance(item, dict) and isinstance(item.get("id"), str)]

    async def test_connection(self) -> bool:
        await self.list_models()
        return True

    async def _post(self, body: dict, timeout: float = 300) -> dict:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{self.base_url}/messages", headers=self._headers(), json=body)
        if resp.status_code >= 400:
            raise RuntimeError(self._http_error(resp))
        return resp.json()

    async def _complete(self, messages: list[dict], model: str) -> str:
        text = reply_text(await self._post(self._body(messages, model)))
        if not text:
            raise RuntimeError("Anthropic returned an empty response")
        return text

    async def _stream(self, messages: list[dict], model: str):
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/messages",
                headers=self._headers(),
                json=self._body(messages, model, stream=True),
            ) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    raise RuntimeError(self._http_error(resp))
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
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
        except Exception:
            if yielded:
                raise
            yield await self._complete(messages, self.listing_model)
            return
        if not yielded:
            yield await self._complete(messages, self.listing_model)

    async def vision_chat(self, messages: list[dict]):
        yield await self._complete(messages, self.vision_model)

    async def quick_chat(self, messages: list[dict]):
        yield await self._complete(messages, self.listing_model)

    async def web_search(self, messages: list[dict]) -> dict:
        body = self._body(messages, self.listing_model, tools=[WEB_SEARCH_TOOL])
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
