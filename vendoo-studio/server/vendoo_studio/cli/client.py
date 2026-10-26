"""HTTP calls to one Anteroom server."""

from __future__ import annotations

import json
import mimetypes
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

from vendoo_studio.cli.constants import CLI_NAME, DEFAULT_TIMEOUT
from vendoo_studio.cli.errors import ApiError, ConnectFailed

_BINARY_PREFIXES = ("image/", "audio/", "video/", "application/octet-stream", "application/pdf", "application/zip")


class AnteroomClient:
    def __init__(self, base_url: str, token: str | None = None, *, timeout: float = DEFAULT_TIMEOUT, transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.transport = transport

    def call(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        json_body: Any = None,
        form: dict[str, Any] | None = None,
        files: list[tuple[str, Path]] | None = None,
        headers: dict[str, str] | None = None,
        stream_seconds: float | None = None,
        output: str | None = None,
    ) -> dict[str, Any]:
        url = join_url(self.base_url, path)
        request_headers = {"User-Agent": f"{CLI_NAME}-cli", "Accept": "application/json, text/event-stream, */*"}
        if self.token:
            request_headers["Authorization"] = f"Bearer {self.token}"
        if headers:
            request_headers.update(headers)
        params = _query(query)
        opened = _open_files(files or [])
        timeout = httpx.Timeout(self.timeout, connect=min(10.0, self.timeout))
        try:
            with httpx.Client(transport=self.transport, timeout=timeout, follow_redirects=True) as client:
                if stream_seconds is not None:
                    return self._stream(
                        client, method, url, params=params, json_body=json_body, form=form,
                        files=opened, headers=request_headers, stream_seconds=stream_seconds, output=output,
                    )
                response = client.request(
                    method.upper(), url, params=params, json=json_body, data=form or None,
                    files=opened or None, headers=request_headers,
                )
                return _interpret(response, output)
        except httpx.TimeoutException as exc:
            raise ConnectFailed(f"Timed out talking to {self.base_url} after {self.timeout:g}s") from exc
        except httpx.HTTPError as exc:
            raise ConnectFailed(f"Could not reach {self.base_url} ({exc.__class__.__name__})") from exc
        finally:
            for _field, payload in opened:
                payload[1].close()

    def _stream(
        self,
        client: httpx.Client,
        method: str,
        url: str,
        *,
        params: list[tuple[str, str]],
        json_body: Any,
        form: dict[str, Any] | None,
        files: list[tuple[str, tuple]],
        headers: dict[str, str],
        stream_seconds: float,
        output: str | None,
    ) -> dict[str, Any]:
        with client.stream(
            method.upper(), url, params=params, json=json_body, data=form or None,
            files=files or None, headers=headers,
        ) as response:
            content_type = response.headers.get("content-type", "")
            if "text/event-stream" not in content_type:
                body = b"".join(response.iter_bytes())
                finished = httpx.Response(response.status_code, headers=response.headers, content=body)
                return _interpret(finished, output)
            events: list[Any] = []
            raw_lines: list[str] = []
            data_lines: list[str] = []
            truncated = False
            deadline = time.monotonic() + stream_seconds if stream_seconds > 0 else None
            for line in response.iter_lines():
                if deadline is not None and time.monotonic() > deadline:
                    truncated = True
                    break
                raw_lines.append(line)
                if line == "":
                    _flush_event(data_lines, events)
                    data_lines = []
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
            _flush_event(data_lines, events)
            raw = "\n".join(raw_lines)
            if output:
                Path(output).expanduser().write_text(raw, encoding="utf-8")
            payload = {"events": events, "truncated": truncated}
            if response.status_code >= 400:
                raise ApiError(response.status_code, _detail_message(payload) or response.reason_phrase, payload)
            return {"status": response.status_code, "ok": True, "data": payload, "error": None}
        raise ConnectFailed(f"Could not read a stream from {self.base_url}")


def join_url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def _query(query: dict[str, Any] | None) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for key, value in (query or {}).items():
        if value is None:
            continue
        if isinstance(value, list):
            for item in value:
                pairs.append((key, _stringify(item)))
        else:
            pairs.append((key, _stringify(value)))
    return pairs


def _stringify(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _open_files(files: list[tuple[str, Path]]) -> list[tuple[str, tuple]]:
    opened = []
    for field, path in files:
        handle = path.open("rb")
        guessed = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        opened.append((field, (path.name, handle, guessed)))
    return opened


def _interpret(response: httpx.Response, output: str | None) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    body = response.content
    if output:
        destination = Path(output).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(body)
    data: Any
    if _is_binary(content_type):
        saved = output
        if not saved:
            handle = tempfile.NamedTemporaryFile(prefix="anteroom-", suffix=_suffix(content_type), delete=False)
            handle.write(body)
            handle.close()
            saved = handle.name
        data = {"path": saved, "content_type": content_type.split(";")[0], "bytes": len(body)}
    else:
        data = _decode(body, content_type)
    if response.status_code >= 400:
        raise ApiError(response.status_code, _detail_message(data) or response.reason_phrase, data)
    return {"status": response.status_code, "ok": True, "data": data, "error": None}


def _is_binary(content_type: str) -> bool:
    media = content_type.split(";")[0].strip().lower()
    return any(media.startswith(prefix) if prefix.endswith("/") else media == prefix for prefix in _BINARY_PREFIXES)


def _suffix(content_type: str) -> str:
    media = content_type.split(";")[0].strip().lower()
    return mimetypes.guess_extension(media) or ".bin"


def _decode(body: bytes, content_type: str) -> Any:
    if not body:
        return None
    text = body.decode("utf-8", errors="replace")
    media = content_type.split(";")[0].strip().lower()
    if "json" in media or not media or text[:1] in {"{", "["}:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    return text


def _detail_message(data: Any) -> str:
    if isinstance(data, dict):
        detail = data.get("detail")
        if isinstance(detail, str):
            return detail
        if isinstance(detail, list):
            parts = []
            for item in detail:
                if isinstance(item, dict):
                    loc = ".".join(str(piece) for piece in item.get("loc") or [])
                    parts.append(f"{loc}: {item.get('msg')}".strip(": "))
                else:
                    parts.append(str(item))
            if parts:
                return "; ".join(parts)
        if isinstance(data.get("error"), str):
            return data["error"]
    if isinstance(data, str) and data.strip():
        return data.strip()[:500]
    return ""


def _flush_event(data_lines: list[str], events: list[Any]) -> None:
    if not data_lines:
        return
    text = "\n".join(data_lines)
    if text == "[DONE]":
        events.append({"done": True})
        return
    try:
        events.append(json.loads(text))
    except json.JSONDecodeError:
        events.append(text)
