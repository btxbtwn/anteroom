"""Request/response calls to the Anteroom Bridge in Chrome.

``request`` sends one message over the extension WebSocket and waits for the
matching ``browser.result``. Direct marketplace actions, reads and checks all
go through it.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from vendoo_studio.models.protocol import ProtocolMessage
from vendoo_studio.services.marketplace_diagnostics import failure_context
from vendoo_studio.services.marketplace_errors import report_exception, report_result

READ_TIMEOUT_SEC = 20.0


class BrowserBridgeError(RuntimeError):
    pass


def _manager():
    from vendoo_studio.services.extension_connection import current_extension

    return current_extension()


async def request(job, message_type: str, payload: dict[str, Any] | None = None, *, timeout: float = READ_TIMEOUT_SEC) -> dict:
    started = time.monotonic()
    marketplace = str((payload or {}).get("marketplace") or "chrome")
    try:
        result = await _request(job, message_type, payload, timeout=timeout)
    except Exception as exc:
        report_exception(marketplace, message_type, exc, context=failure_context(
            message_type, payload, None, time.monotonic() - started, timeout))
        raise
    report_result(marketplace, message_type, result, context=failure_context(
        message_type, payload, result, time.monotonic() - started, timeout))
    return result


async def _request(job, message_type: str, payload: dict[str, Any] | None = None, *, timeout: float = READ_TIMEOUT_SEC) -> dict:
    if message_type in {"job.vendoo_api", "marketplace.execute", "marketplace.delete", "marketplace.promote"}:
        from vendoo_studio.services import listing_mode

        # A hosted seller's mode is read from the database: keep that off the event loop.
        await asyncio.to_thread(listing_mode.require, "vendoo" if message_type == "job.vendoo_api" else "direct")
    manager = _manager()
    # A hosted relay may read the device route from Postgres: keep it off the event loop.
    if not await asyncio.to_thread(lambda: manager.connected):
        raise BrowserBridgeError("Connect Chrome first.")
    owner = getattr(manager, "owner", None)
    if owner:
        from vendoo_studio.services.device_routes import RoutingUnavailable, forward_target, post
        try:
            target = await forward_target(owner, "chrome")
        except RoutingUnavailable:
            raise BrowserBridgeError("Could not reach the Chrome extension.") from None
        if target is not None:
            # One attempt. A lost reply stays uncertain; it is not sent again.
            request_id = uuid.uuid4().hex
            message = ProtocolMessage(
                type=message_type, job_id=job.id, message_id=request_id,
                payload={**(payload or {}), "job_id": job.id, "request_id": request_id},
            ).model_dump(mode="json")
            try:
                body = await post(target, "/internal/browser-relay/request", {
                    "user_id": owner, "message": message, "timeout": timeout,
                }, timeout=timeout + 5)
            except RoutingUnavailable:
                raise BrowserBridgeError("Could not reach the Chrome extension.") from None
            if not body.get("ok"):
                raise BrowserBridgeError(body.get("error") or "Could not reach the Chrome extension.")
            reply = body.get("reply")
            return reply if isinstance(reply, dict) else {"ok": False, "error": "Malformed browser reply"}
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
