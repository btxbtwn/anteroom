"""Direct marketplace List, Update, Check, Reset and Cancel endpoints."""
from __future__ import annotations

from collections.abc import Awaitable
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from anteroom.database import get_db
from anteroom.services import native_marketplaces as native
from anteroom.services.native_marketplaces import CheckRequest, ListRequest, Marketplace

router = APIRouter(prefix="/api/conversations/{conv_id}/native-marketplaces", tags=["native-marketplaces"])


class ResetRequest(BaseModel):
    """The seller confirms they looked on the marketplace themselves."""
    confirm: bool = False


class SaveDraftRequest(BaseModel):
    model_config = {"extra": "forbid"}
    revision_id: str


# Why a refusal happened, beside its plain-text message: the app lets a confirmed
# action wait its turn when the reason is native.BUSY.
REASON_HEADER = "X-Anteroom-Reason"


def _http(exc: native.NativeMarketplaceError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc), headers={REASON_HEADER: exc.reason} if exc.reason else None)


async def _reply(call: Awaitable[dict]) -> dict:
    try:
        return await call
    except native.NativeMarketplaceError as exc:
        raise _http(exc) from exc


def _sync(call, *args):
    try:
        return call(*args)
    except native.NativeMarketplaceError as exc:
        raise _http(exc) from exc


@router.get("")
def status(conv_id: str, db: Session = Depends(get_db, scope="function")):
    return _sync(native.status, conv_id, db)


@router.get("/preflight")
async def preflight(conv_id: str, revision_id: str | None = None):
    """What each marketplace still needs before List, for every marketplace in one answer.
    Requests asking at once share one build in one short worker session."""
    return await _reply(native.shared_preflight(conv_id, revision_id))


# These actions wait on Chrome or a marketplace. They take no request session: the service runs
# each database step on a worker thread with a short session of its own.
@router.post("/{marketplace}/list")
async def list_now(conv_id: str, marketplace: Marketplace, body: ListRequest):
    return await _reply(native.list_now(conv_id, marketplace, body, require_ad_rate=True))


@router.post("/{marketplace}/update")
async def update_live(conv_id: str, marketplace: Marketplace, body: ListRequest):
    return await _reply(native.update_live(conv_id, marketplace, body, require_ad_rate=True))


@router.post("/{marketplace}/draft")
async def save_draft(conv_id: str, marketplace: Marketplace, body: SaveDraftRequest):
    return await _reply(native.save_draft(conv_id, marketplace, body.revision_id))


@router.post("/{marketplace}/cancel")
async def cancel(conv_id: str, marketplace: Marketplace):
    return await _reply(native.cancel(conv_id, marketplace))


@router.get("/{marketplace}/form")
async def editor_form(conv_id: str, marketplace: Literal["ebay", "poshmark", "mercari", "depop", "etsy"],
                      revision_id: str | None = None):
    from anteroom.services import native_editor
    from anteroom.services.choice_lists import share

    # Read the DB snapshot in a worker session and release it before awaiting
    # eBay, Etsy or Mercari on the server loop (reusing shared_http).
    try:
        return share(await native_editor.editor_form(conv_id, marketplace, revision_id))
    except native.NativeMarketplaceError as exc:
        raise _http(exc) from exc


@router.post("/{marketplace}/check")
async def check_result(conv_id: str, marketplace: Marketplace, body: CheckRequest | None = None):
    return await _reply(native.check_result(conv_id, marketplace, body))


@router.post("/{marketplace}/form/refresh")
async def refresh_form(conv_id: str, marketplace: Literal["ebay", "poshmark", "mercari", "depop", "etsy"],
                       body: SaveDraftRequest):
    from anteroom.services import native_editor

    return await _reply(native_editor.refresh_editor_form(conv_id, marketplace, body.revision_id))


@router.post("/{marketplace}/reset")
def reset(conv_id: str, marketplace: Marketplace, body: ResetRequest, db: Session = Depends(get_db, scope="function")):
    if body.confirm is not True:
        raise HTTPException(400, "Confirm that you checked the marketplace yourself.")
    return _sync(native.reset, conv_id, marketplace, db)


