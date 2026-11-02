"""Request/response calls to the Anteroom Bridge in Chrome.

``request`` sends one message over the extension WebSocket and waits for the
matching ``browser.result``. Direct marketplace actions, reads and checks all
go through it.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from vendoo_studio.models.protocol import ProtocolMessage

READ_TIMEOUT_SEC = 20.0


class BrowserBridgeError(RuntimeError):
    pass


def _manager():
    from vendoo_studio.services.extension_connection import current_extension

    return current_extension()


async def request(job, message_type: str, payload: dict[str, Any] | None = None, *, timeout: float = READ_TIMEOUT_SEC) -> dict:
    manager = _manager()
    if not manager.connected:
        raise BrowserBridgeError("Connect Chrome first.")
    request_id = uuid.uuid4().hex
    waiter = manager.register_wait(request_id, getattr(job, "id", None))
    sent = await manager.send_message(ProtocolMessage(
        type=message_type,
        job_id=job.id,
        message_id=request_id,
        payload={**(payload or {}), "job_id": job.id, "request_id": request_id},
    ).model_dump(mode="json"))
    if not sent:
        manager.cancel_wait(request_id)
        raise BrowserBridgeError("Could not reach the Chrome extension.")
    try:
        result = await asyncio.wait_for(waiter, timeout)
    except asyncio.CancelledError as exc:
        manager.cancel_wait(request_id)
        raise BrowserBridgeError("The request was cancelled.") from exc
    except TimeoutError as exc:
        manager.cancel_wait(request_id)
        raise BrowserBridgeError("Chrome did not answer in time.") from exc
    return result if isinstance(result, dict) else {"ok": False, "error": "Malformed browser reply"}
