"""Hosted API routes for seller cloud marketplace sessions.

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

from fastapi import APIRouter, Header, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from anteroom import database
from anteroom.database import in_worker_session
from anteroom.routes.api_contract import MarketplaceCloudResult, MarketplaceCloudSessions
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


@router.get("", response_model=MarketplaceCloudSessions)
async def list_cloud_sessions(response: Response):
    enabled = sorted(cloud_sessions.enabled_marketplaces())
    sessions = await in_worker_session(lambda db: cloud_sessions.public_sessions(db))
    response.headers["Cache-Control"] = "no-store"
    return {"enabled": enabled, "sessions": sessions}


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

    upload = await asyncio.to_thread(
        cloud_sessions.consume_upload_token, database.engine, market, x_cloud_upload_token,
    )
    if upload is None:
        raise HTTPException(401, "This connection token expired or was already used. Start connecting again.")

    with use(for_seller(upload.seller_id)):
        try:
            cookies, _, _, user_agent = cloud_sessions.validate_and_normalize_cookies(market, body.cookies, body.user_agent)
            verify = market != "poshmark" or await in_worker_session(cloud_sessions.needs_automation_resume, market)
            label = None
            if verify:
                outcome = await cloud_marketplace.verify_upload(market, cookies, user_agent, upload.proxy_session_id or "")
                result = outcome.get("result") or {}
                session = result.get("session") or {}
                if not result.get("ok") or session.get("state") != "signed_in":
                    raise HTTPException(409, result.get("error") or f"Could not verify the {market.title()} cloud sign-in.")
                label = session.get("label")
                if outcome.get("cookies_changed") and outcome.get("cookies"):
                    cookies = outcome["cookies"]
            summary = await in_worker_session(lambda db: cloud_sessions.save_uploaded_session(
                db, market, cookies, user_agent, upload=upload, verified_label=label, verified=verify,
            ))
        except cloud_sessions.CloudSessionError as exc:
            raise HTTPException(400, str(exc)) from None
        except cloud_marketplace.CloudExecutionUnavailable as exc:
            raise HTTPException(503, str(exc)) from None
    return JSONResponse({"ok": True, "session": summary}, headers={"Cache-Control": "no-store"})


@router.delete("/{marketplace}", response_model=MarketplaceCloudResult)
async def disconnect_cloud_session(marketplace: str, response: Response):
    market = str(marketplace or "").lower()
    if market not in cloud_sessions.SUPPORTED_CLOUD_MARKETPLACES:
        raise HTTPException(404, "Unknown cloud marketplace.")
    try:
        summary = await in_worker_session(cloud_sessions.revoke_session, market)
    except cloud_sessions.CloudSessionError as exc:
        raise HTTPException(400, str(exc)) from None
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True, "session": summary}


async def _verify(market: str, generation: str | None):
    result = await cloud_marketplace.verify_session(market)
    verified = result.get("session") or {}
    summary = await in_worker_session(lambda db: cloud_sessions.public_session(market, db))
    if (result.get("ok") and verified.get("state") == "signed_in" and summary
            and summary.get("connected_at") == generation and summary.get("cloud_connected")):
        return summary, None
    return summary, result.get("error") or f"Could not verify the {market.title()} cloud sign-in."


@router.post("/{marketplace}/verify", response_model=MarketplaceCloudResult)
async def verify_cloud_session(marketplace: str, response: Response):
    market = _ensure_enabled(marketplace)
    previous = await in_worker_session(lambda db: cloud_sessions.public_session(market, db))
    summary, error = await _verify(market, (previous or {}).get("connected_at"))
    response.headers["Cache-Control"] = "no-store"
    return {"ok": error is None, "session": summary, "error": error}
