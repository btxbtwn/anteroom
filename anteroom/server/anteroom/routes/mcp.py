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
from sqlalchemy import event

from anteroom.database import in_worker_session
from anteroom.routes import mcp_photo_uploads
from anteroom.services import (
    mcp_catalog_tools,
    mcp_feedback_tools,
    mcp_http,
    mcp_import_match_tools,
    mcp_marketplace_tools,
    mcp_oauth,
    mcp_photo_tools,
    mcp_reading_tools,
    mcp_tokens,
)
from anteroom.services.hosted_limits import read_bounded_body
from anteroom.services.local_requests import came_through_tailnet

router = APIRouter(prefix="/api/mcp", tags=["mcp"])
router.include_router(mcp_photo_uploads.router)

_MAX_MCP_BODY_BYTES = 2 * 1024 * 1024
_MCP_RPC_TIMEOUT_SECONDS = 30.0
_MCP_STORAGE_CLEANUP_TIMEOUT_SECONDS = 15.0
# Time kept at the end of a call to send its reply after marketplace work stops waiting.
_MCP_REPLY_MARGIN_SECONDS = 2.0
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
    hosted_token_id: str | None,
    oauth_client_id: str | None = None,
    bearer_token: str | None,
    client_key: str,
    session_id: str | None,
    protocol_version: str | None,
    deferred_deletes: list[str],
    expired: Callable[[], bool],
) -> tuple[Any | None, int, dict[str, str]]:
    if expired():
        raise TimeoutError

    def refuse_late_commit(_session) -> None:
        # The client was already told this call timed out: a change still in
        # progress must not be saved behind its back, or a retry doubles it.
        if expired():
            raise TimeoutError

    event.listen(db, "before_commit", refuse_late_commit)
    # A commit flushes after before_commit, and a flush can wait on a locked
    # database. Check again once the writes are flushed, just before COMMIT.
    event.listen(db, "after_flush", lambda session, _context: refuse_late_commit(session))
    token_id = hosted_token_id
    if not is_hosted and bearer_token is not None:
        if not bearer_token:
            mcp_tokens.record_auth_failure(client_key)
            raise HTTPException(401, "Invalid bearer token")
        verified = mcp_tokens.authenticate_token(db.get_bind(), bearer_token, hosted=False)
        if verified is None:
            mcp_tokens.record_auth_failure(client_key)
            raise HTTPException(401, "Invalid or revoked MCP access token")
        token_id = verified.token_id
    # Read fresh on every request, so a change in Settings applies to the next call.
    # A seller session, or loopback on the Mac without a token, reaches every tool.
    if oauth_client_id is not None:
        # An app connected by signing in: what the seller lets it reach, read fresh.
        permissions = mcp_oauth.permissions_for(db, oauth_client_id)
        if permissions is None:
            raise HTTPException(403, "This app was disconnected in Anteroom Settings. Connect it again to use it.")
    else:
        permissions = mcp_tokens.permissions_for(db, token_id) if token_id is not None else None
    agent = mcp_marketplace_tools.agent_label(db, token_id=token_id, oauth_client_id=oauth_client_id)
    return mcp_http.handle_rpc_payload(
        db,
        payload,
        session_id=session_id,
        protocol_version=protocol_version,
        bearer_token=bearer_token,
        permissions=permissions,
        deferred_deletes=deferred_deletes,
        expired=expired,
        agent=agent,
    )


@router.post("")
async def mcp_post(request: Request):
    is_hosted = getattr(request.state, "seller", None) is not None or hasattr(request.app.state, "session_verifier")
    hosted_token_id = getattr(request.state, "mcp_token_id", None) if is_hosted else None
    oauth_client_id = getattr(request.state, "mcp_oauth_client_id", None) if is_hosted else None
    bearer = _extract_bearer(request)
    client_key = _client_key(request, is_hosted=is_hosted)
    if not is_hosted and bearer is None and came_through_tailnet(request.headers):
        # Only the Mac itself reaches every tool without a token. An agent on
        # another tailnet device is held to the token the seller gave it.
        return JSONResponse(
            {"detail": "Use an MCP access token from Settings → Agents to reach Anteroom over Tailscale."},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
        )
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
    # Upload links a tool call makes point back at the server the client reached.
    mcp_photo_tools.request_origin.set(mcp_photo_tools.origin_for(request, hosted=is_hosted))

    # A size-tag or measurement reading waits on a photo model: those calls get longer.
    call_timeout = mcp_reading_tools.call_timeout(payload, _MCP_RPC_TIMEOUT_SECONDS)
    deadline = monotonic() + call_timeout
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
                    hosted_token_id=hosted_token_id,
                    oauth_client_id=oauth_client_id,
                    bearer_token=bearer,
                    client_key=client_key,
                    session_id=session_id,
                    protocol_version=protocol_version,
                    deferred_deletes=deferred_deletes,
                    expired=expired,
                )
            )
            pending = mcp_http.pending_photos(outcome[0])
            if pending:
                # Photo bytes come from Storage: read them after the session is closed,
                # leaving a moment to send the reply before the call's deadline.
                await asyncio.to_thread(mcp_http.load_pending_photos, pending, deadline=deadline - 1.0)
            links = mcp_import_match_tools.pending_links(outcome[0])
            if links:
                # Confirming an import match reads the marketplace: only once the session is closed.
                await mcp_import_match_tools.finish_pending_links(links, deadline=deadline - _MCP_REPLY_MARGIN_SECONDS)
            actions = mcp_marketplace_tools.pending_actions(outcome[0])
            if actions:
                # Marketplace work waits on Chrome, a cloud session, eBay or Etsy: only now that
                # the session is closed. Work still running at the deadline keeps going.
                await mcp_marketplace_tools.run_pending(actions, deadline=deadline - _MCP_REPLY_MARGIN_SECONDS)
            feedback = mcp_feedback_tools.pending_deliveries(outcome[0])
            if feedback:
                # Email goes out after the session is closed, within the call's deadline.
                await mcp_feedback_tools.deliver_pending(feedback, deadline=deadline - _MCP_REPLY_MARGIN_SECONDS)
            uploads = mcp_photo_tools.pending_uploads(outcome[0])
            if uploads:
                # Photos sent to add_listing_photos are stored with the session closed, then saved.
                await mcp_photo_tools.finish_pending_uploads(uploads, expired=expired, request=request)
            forms = mcp_catalog_tools.pending_form_reads(outcome[0])
            if forms:
                await mcp_catalog_tools.finish_form_reads(forms, deadline=deadline - _MCP_REPLY_MARGIN_SECONDS)
            readings = mcp_reading_tools.pending_reads(outcome[0])
            if readings:
                # Photos are fetched and read by a photo model only once the session is closed.
                await mcp_reading_tools.finish_reads(
                    readings, deadline=deadline - _MCP_REPLY_MARGIN_SECONDS, request=request if is_hosted else None)
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
                            server_key=oauth_client_id is not None,
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
            result, timeout=call_timeout,
        )
    except TimeoutError:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32000, "message": "MCP request timed out. Anything it had not saved by then was not saved."}},
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
