"""Studio's outbound connection to Anteroom's relay.

The relay is the only public address eBay and Etsy can reach; Studio stays on
127.0.0.1 and connects out. This Mac registers once as a device and keeps its
token in the Keychain. While a marketplace account is connected, a WebSocket
to the relay's stream delivers sale events as they land; each is recorded,
then acknowledged so the relay forgets it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx
import websockets

from vendoo_studio.config import RELAY_URL
from vendoo_studio.services.keychain import get_relay_device, set_relay_device

log = logging.getLogger("vendoo_studio.relay")

RECONNECT_BASE_S = 2
RECONNECT_MAX_S = 300
PING_INTERVAL_S = 30


class RelayError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class StreamState:
    connected: bool = False
    last_error: str | None = None
    last_event_at: float | None = None
    connected_at: float | None = None


_state = StreamState()
_task: asyncio.Task | None = None


def stream_state() -> dict[str, Any]:
    return {
        "running": bool(_task and not _task.done()),
        "connected": _state.connected,
        "lastError": _state.last_error,
        "lastEventAt": _state.last_event_at,
    }


async def ensure_device() -> dict:
    device = get_relay_device()
    if device:
        return device
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(f"{RELAY_URL}/devices")
    if resp.status_code == 429:
        raise RelayError("Anteroom's relay is busy. Try again in a minute.", 429)
    if resp.status_code != 201:
        raise RelayError(f"Could not register this Mac with Anteroom's relay ({resp.status_code}).", resp.status_code)
    body = resp.json()
    device = {"deviceId": body["deviceId"], "token": body["token"]}
    set_relay_device(device)
    return device


def _auth_headers(device: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {device['token']}"}


async def relay_request(method: str, path: str, *, json_body: Any = None, headers: dict | None = None,
                        params: dict | None = None, data: dict | None = None, files: dict | None = None,
                        timeout: float = 30) -> httpx.Response:
    """One authenticated call to the relay's /studio API."""
    device = await ensure_device()
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.request(
            method,
            f"{RELAY_URL}{path}",
            json=json_body,
            **({"data": data} if data is not None else {}),
            **({"files": files} if files is not None else {}),
            params=params,
            headers={**_auth_headers(device), **(headers or {})},
        )


async def _process(events: list[dict], handle) -> int | None:
    """Record each event; return the highest seq that is safe to acknowledge."""
    through = None
    for event in events:
        seq = event.get("seq")
        try:
            await handle(event)
        except Exception:  # noqa: BLE001 - keep it unacknowledged; the relay resends it
            log.exception("relay event %s could not be recorded", seq)
            break
        through = seq
        _state.last_event_at = time.time()
    return through


async def _stream_once(handle) -> None:
    device = await ensure_device()
    url = RELAY_URL.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/studio/stream?after=0"
    async with websockets.connect(
        url,
        additional_headers=_auth_headers(device),
        ping_interval=PING_INTERVAL_S,
        open_timeout=20,
    ) as socket:
        _state.connected = True
        _state.connected_at = time.time()
        _state.last_error = None
        log.info("connected to the relay")
        async for raw in socket:
            message = json.loads(raw)
            if message.get("type") != "events" or not message.get("events"):
                continue
            through = await _process(message["events"], handle)
            if through is not None:
                await socket.send(json.dumps({"type": "ack", "through": through}))


async def _run(handle) -> None:
    attempt = 0
    while True:
        try:
            await _stream_once(handle)
            attempt = 0
        except asyncio.CancelledError:
            raise
        except websockets.InvalidStatus as exc:
            status = exc.response.status_code
            _state.last_error = "This Mac is no longer registered with the relay." if status == 401 else f"Relay refused the connection ({status})."
            log.warning("relay stream refused: %s", status)
            if status == 401:
                return
        except Exception as exc:  # noqa: BLE001 - offline, relay restart, etc.
            _state.last_error = "Can't reach Anteroom's relay right now; retrying."
            log.info("relay stream dropped: %s", exc)
        finally:
            _state.connected = False
        # A healthy connection that drops reconnects quickly; repeated failures back off.
        if _state.connected_at and time.time() - _state.connected_at > 60:
            attempt = 0
        delay = min(RECONNECT_MAX_S, RECONNECT_BASE_S * 2 ** attempt) * (0.5 + random.random() / 2)
        attempt += 1
        await asyncio.sleep(delay)


def start_stream(handle) -> asyncio.Task | None:
    """Start listening, once. Call from the server's event loop."""
    global _task
    if _task and not _task.done():
        return _task
    if not get_relay_device():
        return None
    _task = asyncio.get_running_loop().create_task(_run(handle), name="relay-stream")
    return _task


def stop_stream() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
    _task = None
    _state.connected = False
