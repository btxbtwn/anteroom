"""Streamable HTTP MCP endpoint (``/api/mcp``) for Hosted and Desktop Anteroom.

On hosted ``myanteroom.com``, ``SellerSessionMiddleware`` authenticates the
caller (via seller-scoped ``arm_`` personal access token or seller session JWT)
and binds PostgreSQL RLS before this router runs.

On local Mac desktop, loopback requests work directly, and if an
``Authorization: Bearer`` token is supplied it is verified against the seller's
local MCP access tokens.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from threading import Event
from time import monotonic
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from anteroom.database import in_worker_session
from anteroom.services import mcp_http, mcp_tokens
from anteroom.services.hosted_limits import read_bounded_body

router = APIRouter(prefix="/api/mcp", tags=["mcp"])

_MAX_MCP_BODY_BYTES = 2 * 1024 * 1024
_MCP_RPC_TIMEOUT_SECONDS = 30.0
_MCP_STORAGE_CLEANUP_TIMEOUT_SECONDS = 15.0
_active_calls: set[asyncio.Task] = set()


def _finish_call(worker: asyncio.Task) -> None:
    _active_calls.discard(worker)
    if not worker.cancelled():
        worker.exception()


def _client_key(request: Request, *, is_hosted: bool = False) -> str:
    if is_hosted:
        forwarded = (request.headers.get("fly-client-ip") or "").strip()
        if forwarded:
            return forwarded
    return request.client.host if request.client and request.client.host else "unknown"


def _extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if not auth:
        return None
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return ""
    return token.strip()


def _wants_sse_only(accept_header: str) -> bool:
    lowered = (accept_header or "").lower()
    return "text/event-stream" in lowered and "application/json" not in lowered and "*/*" not in lowered


def _run_mcp_call(
    db,
    payload: Any,
    *,
    is_hosted: bool,
    bearer_token: str | None,
    client_key: str,
    session_id: str | None,
    protocol_version: str | None,
    deferred_deletes: list[str],
    expired: Callable[[], bool],
) -> tuple[Any | None, int, dict[str, str]]:
    if expired():
        raise TimeoutError
    if not is_hosted and bearer_token is not None:
        if not bearer_token:
            mcp_tokens.record_auth_failure(client_key)
            raise HTTPException(401, "Invalid bearer token")
        verified = mcp_tokens.authenticate_token(db.get_bind(), bearer_token, hosted=False)
        if verified is None:
            mcp_tokens.record_auth_failure(client_key)
            raise HTTPException(401, "Invalid or revoked MCP access token")
    return mcp_http.handle_rpc_payload(
        db,
        payload,
        session_id=session_id,
        protocol_version=protocol_version,
        bearer_token=bearer_token,
        deferred_deletes=deferred_deletes,
        expired=expired,
    )


@router.post("")
async def mcp_post(request: Request):
    is_hosted = getattr(request.state, "seller", None) is not None or hasattr(request.app.state, "session_verifier")
    bearer = _extract_bearer(request)
    client_key = _client_key(request, is_hosted=is_hosted)
    if not is_hosted and bearer is not None and mcp_tokens.is_auth_rate_limited(client_key):
        return JSONResponse(
            {"detail": "Too many failed authentication attempts. Try again shortly."},
            status_code=429,
            headers={"Retry-After": "60", "Cache-Control": "no-store"},
        )

    raw_body = await read_bounded_body(
        request,
        _MAX_MCP_BODY_BYTES,
        too_large_detail="MCP request body is too large",
    )
    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
            status_code=400,
            headers={"Cache-Control": "no-store"},
        )

    session_id = request.headers.get("mcp-session-id")
    protocol_version = request.headers.get("mcp-protocol-version")
    deferred_deletes: list[str] = []

    deadline = monotonic() + _MCP_RPC_TIMEOUT_SECONDS
    stopped = Event()

    def expired() -> bool:
        return stopped.is_set() or monotonic() >= deadline

    result: asyncio.Future[tuple[Any | None, int, dict[str, str]]] = asyncio.get_running_loop().create_future()

    async def run_and_cleanup():
        try:
            outcome = await in_worker_session(
                lambda db: _run_mcp_call(
                    db,
                    payload,
                    is_hosted=is_hosted,
                    bearer_token=bearer,
                    client_key=client_key,
                    session_id=session_id,
                    protocol_version=protocol_version,
                    deferred_deletes=deferred_deletes,
                    expired=expired,
                )
            )
            if not result.done():
                result.set_result(outcome)
        except Exception as exc:
            if not result.done():
                result.set_exception(exc)
        finally:
            # in_worker_session returns or raises after closing its session.
            if deferred_deletes:
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(
                            mcp_http.delete_hosted_storage_keys,
                            deferred_deletes,
                            bearer_token=bearer,
                            deadline_seconds=_MCP_STORAGE_CLEANUP_TIMEOUT_SECONDS,
                        ),
                        timeout=_MCP_STORAGE_CLEANUP_TIMEOUT_SECONDS,
                    )
                except Exception:  # noqa: BLE001 - best-effort cleanup after DB commit
                    pass

    worker = asyncio.create_task(run_and_cleanup())
    # Keep the task alive after the request returns, and consume detached exceptions.
    _active_calls.add(worker)
    worker.add_done_callback(_finish_call)
    try:
        body, status_code, extra_headers = await asyncio.wait_for(
            result, timeout=_MCP_RPC_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32000, "message": "MCP request timed out"}},
            status_code=504,
            headers={"Cache-Control": "no-store"},
        )
    finally:
        stopped.set()

    await asyncio.shield(worker)
    headers = {"Cache-Control": "no-store", **extra_headers}
    if body is None:
        return Response(status_code=status_code, headers=headers)
    if _wants_sse_only(request.headers.get("accept", "")):
        event_payload = f"event: message\ndata: {json.dumps(body, default=str)}\n\n"
        return Response(
            content=event_payload,
            status_code=status_code,
            media_type="text/event-stream",
            headers=headers,
        )
    return JSONResponse(body, status_code=status_code, headers=headers)


@router.get("")
def mcp_get():
    """Streamable HTTP servers without unsolicited SSE streams return 405."""
    return Response(status_code=405, headers={"Allow": "POST, DELETE", "Cache-Control": "no-store"})


@router.delete("")
def mcp_delete():
    """Acknowledge MCP session termination."""
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
