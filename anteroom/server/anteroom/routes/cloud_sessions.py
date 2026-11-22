"""Hosted API routes for seller cloud marketplace sessions (Phase 1: Poshmark).

- ``GET /api/cloud-sessions``: non-secret session status metadata (never decrypts).
- ``POST /api/cloud-sessions/{marketplace}/connect-token``: mints a single-use
  180-second upload token so the Chrome extension can POST cookies directly
  without receiving the seller's Supabase JWT or passing cookies through page JS.
- ``POST /api/cloud-sessions/{marketplace}/upload``: authenticated exclusively
  by the single-use ``X-Cloud-Upload-Token`` header, encrypts cookies into
  Supabase Vault, and returns metadata only.
- ``DELETE /api/cloud-sessions/{marketplace}``: deletes Vault cookies and
  marks the cloud session revoked.
- ``POST /api/cloud-sessions/{marketplace}/verify``: live read-only verification
  executed on the worker process.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from anteroom import database
from anteroom.database import in_worker_session
from anteroom.services import cloud_marketplace, cloud_sessions
from anteroom.services.hosted_context import for_seller
from anteroom.services.service_context import use

router = APIRouter(prefix="/api/cloud-sessions", tags=["cloud-sessions"])


class CloudSessionUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cookies: list[dict[str, Any]] = Field(min_length=1, max_length=100)
    user_agent: str | None = Field(default=None, max_length=300)


def _ensure_enabled(marketplace: str) -> str:
    market = str(marketplace or "").lower()
    if market not in cloud_sessions.SUPPORTED_CLOUD_MARKETPLACES:
        raise HTTPException(404, "Unknown cloud marketplace.")
    if not cloud_sessions.is_cloud_enabled(market):
        raise HTTPException(404, "Cloud sessions are not enabled for this marketplace.")
    return market


@router.get("")
async def list_cloud_sessions():
    enabled = sorted(cloud_sessions.enabled_marketplaces())
    sessions = await in_worker_session(lambda db: cloud_sessions.public_sessions(db))
    return JSONResponse(
        {"enabled": enabled, "sessions": sessions},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/{marketplace}/connect-token")
async def create_connect_token(marketplace: str):
    market = _ensure_enabled(marketplace)
    try:
        token_info = await in_worker_session(cloud_sessions.mint_upload_token, market)
    except cloud_sessions.CloudSessionError as exc:
        raise HTTPException(400, str(exc)) from None
    return JSONResponse(token_info, headers={"Cache-Control": "no-store"})


@router.post("/{marketplace}/upload")
async def upload_cloud_session(
    marketplace: str,
    body: CloudSessionUpload,
    x_cloud_upload_token: str | None = Header(default=None, alias="X-Cloud-Upload-Token"),
):
    market = str(marketplace or "").lower()
    if market not in cloud_sessions.SUPPORTED_CLOUD_MARKETPLACES:
        raise HTTPException(404, "Unknown cloud marketplace.")
    if not cloud_sessions.is_marketplace_configured(market):
        raise HTTPException(404, "Cloud sessions are not enabled for this marketplace.")
    if not x_cloud_upload_token:
        raise HTTPException(401, "A valid one-time upload token is required.")

    seller_id = await asyncio.to_thread(
        cloud_sessions.consume_upload_token, database.engine, market, x_cloud_upload_token,
    )
    if not seller_id:
        raise HTTPException(401, "This connection token expired or was already used. Start connecting again.")

    with use(for_seller(seller_id)):
        try:
            summary = await in_worker_session(
                cloud_sessions.save_uploaded_session, market, body.cookies, body.user_agent,
            )
        except cloud_sessions.CloudSessionError as exc:
            raise HTTPException(400, str(exc)) from None
    return JSONResponse({"ok": True, "session": summary}, headers={"Cache-Control": "no-store"})


@router.delete("/{marketplace}")
async def disconnect_cloud_session(marketplace: str):
    market = str(marketplace or "").lower()
    if market not in cloud_sessions.SUPPORTED_CLOUD_MARKETPLACES:
        raise HTTPException(404, "Unknown cloud marketplace.")
    try:
        summary = await in_worker_session(cloud_sessions.revoke_session, market)
    except cloud_sessions.CloudSessionError as exc:
        raise HTTPException(400, str(exc)) from None
    return JSONResponse({"ok": True, "session": summary}, headers={"Cache-Control": "no-store"})


@router.post("/{marketplace}/verify")
async def verify_cloud_session(marketplace: str):
    market = _ensure_enabled(marketplace)
    result = await cloud_marketplace.verify_session(market)
    summary = await in_worker_session(lambda db: cloud_sessions.public_session(market, db))
    return JSONResponse(
        {"ok": bool(result.get("ok")), "session": summary, "error": result.get("error")},
        headers={"Cache-Control": "no-store"},
    )
