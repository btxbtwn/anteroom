"""``POST /api/mcp/photo-uploads/{upload_id}``: one photo through a single-use upload link.

An MCP client gets the link from ``create_photo_upload_url``. The request
carries no seller session, only the link's secret in ``X-Upload-Token``; on
the hosted API the session middleware lets this one path through for that
reason. The secret is checked and the link spent before the body is read, and
the photo can only go to the listing the link names, as that link's seller.
"""
from __future__ import annotations

import asyncio
from contextlib import nullcontext

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from anteroom import database
from anteroom.services import mcp_photo_tools, mcp_tokens

# Included by routes/mcp.py, under /api/mcp.
router = APIRouter(prefix="/photo-uploads", tags=["mcp"])


def _client_key(request: Request, *, hosted: bool) -> str:
    forwarded = (request.headers.get("fly-client-ip") or "").strip() if hosted else ""
    host = forwarded or (request.client.host if request.client and request.client.host else "unknown")
    return f"photo-upload:{host}"


@router.post("/{upload_id}", status_code=201)
async def upload_photo(upload_id: str, request: Request):
    hosted = hasattr(request.app.state, "session_verifier")
    client_key = _client_key(request, hosted=hosted)
    if mcp_tokens.is_auth_rate_limited(client_key):
        return JSONResponse(
            {"detail": "Too many failed upload attempts. Try again shortly."},
            status_code=429, headers={"Retry-After": "60", "Cache-Control": "no-store"},
        )
    claim = await asyncio.to_thread(
        mcp_photo_tools.consume_upload, database.engine, upload_id,
        request.headers.get(mcp_photo_tools.UPLOAD_HEADER) or "", hosted=hosted,
    )
    if claim is None:
        mcp_tokens.record_auth_failure(client_key)
        raise HTTPException(
            401, "This upload link expired, was already used, or its token is wrong. "
                 "Ask for a new one with create_photo_upload_url.",
        )
    if hosted:
        from anteroom.services.hosted_context import for_seller
        from anteroom.services.service_context import use

        # Storage refuses this request's credentials, so the photo goes in on the
        # server key, which only writes under this seller's own folder.
        seller = use(for_seller(claim.owner_id))
    else:
        seller = nullcontext()
    with seller:
        outcome = await mcp_photo_tools.receive_upload(request, claim.conversation_id)
    return JSONResponse(outcome, status_code=201, headers={"Cache-Control": "no-store"})
