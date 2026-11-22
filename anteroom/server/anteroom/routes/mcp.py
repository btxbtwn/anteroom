"""Streamable HTTP MCP endpoint (``/api/mcp``) for Hosted and Desktop Anteroom.

On hosted ``myanteroom.com``, ``SellerSessionMiddleware`` authenticates the
caller (via seller-scoped ``arm_`` personal access token or seller session JWT)
and binds PostgreSQL RLS before this router runs.

On local Mac desktop, loopback requests work directly, and if an
``Authorization: Bearer`` token is supplied it is verified against the seller's
local MCP access tokens.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from anteroom.database import in_worker_session
from anteroom.services import mcp_http, mcp_tokens
from anteroom.services.hosted_limits import read_bounded_body

router = APIRouter(prefix="/api/mcp", tags=["mcp"])

_MAX_MCP_BODY_BYTES = 2 * 1024 * 1024


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
    session_id: str | None,
    protocol_version: str | None,
) -> tuple[Any | None, int, dict[str, str]]:
    if not is_hosted and bearer_token is not None:
        if not bearer_token:
            raise HTTPException(401, "Invalid bearer token")
        verified = mcp_tokens.authenticate_token(db.get_bind(), bearer_token, hosted=False)
        if verified is None:
            raise HTTPException(401, "Invalid or revoked MCP access token")
    return mcp_http.handle_rpc_payload(
        db,
        payload,
        session_id=session_id,
        protocol_version=protocol_version,
        bearer_token=bearer_token,
    )


@router.post("")
async def mcp_post(request: Request):
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

    is_hosted = getattr(request.state, "seller", None) is not None
    bearer = _extract_bearer(request)
    session_id = request.headers.get("mcp-session-id")
    protocol_version = request.headers.get("mcp-protocol-version")

    body, status_code, extra_headers = await in_worker_session(
        lambda db: _run_mcp_call(
            db,
            payload,
            is_hosted=is_hosted,
            bearer_token=bearer,
            session_id=session_id,
            protocol_version=protocol_version,
        )
    )
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
