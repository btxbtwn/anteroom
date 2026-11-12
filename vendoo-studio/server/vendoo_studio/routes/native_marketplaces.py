"""Direct marketplace List, Update, Check, Reset and Cancel endpoints."""
from __future__ import annotations

from collections.abc import Awaitable
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.services import native_marketplaces as native
from vendoo_studio.services.native_marketplaces import CheckRequest, InspectionMarketplace, InspectRequest, ListRequest, Marketplace

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
def preflight(conv_id: str, db: Session = Depends(get_db, scope="function")):
    """What each marketplace still needs before List. A plain function, so FastAPI runs
    its database work in the threadpool instead of on the event loop."""
    return _sync(native.preflight, conv_id, db)


# These actions wait on Chrome or a marketplace. They take no request session: the service runs
# each database step on a worker thread with a short session of its own.
@router.post("/{marketplace}/inspect")
async def inspect_page(conv_id: str, marketplace: InspectionMarketplace, body: InspectRequest):
    return await _reply(native.inspect_page(conv_id, marketplace, body))


@router.post("/{marketplace}/list")
async def list_now(conv_id: str, marketplace: Marketplace, body: ListRequest):
    return await _reply(native.list_now(conv_id, marketplace, body))


@router.post("/{marketplace}/update")
async def update_live(conv_id: str, marketplace: Marketplace, body: ListRequest):
    return await _reply(native.update_live(conv_id, marketplace, body))


@router.post("/{marketplace}/draft")
async def save_draft(conv_id: str, marketplace: Marketplace, body: SaveDraftRequest):
    return await _reply(native.save_draft(conv_id, marketplace, body.revision_id))


@router.post("/{marketplace}/cancel")
async def cancel(conv_id: str, marketplace: Marketplace):
    return await _reply(native.cancel(conv_id, marketplace))


@router.get("/{marketplace}/form")
def editor_form(conv_id: str, marketplace: Literal["ebay", "poshmark", "mercari", "depop", "etsy"],
                revision_id: str | None = None, db: Session = Depends(get_db, scope="function")):
    from vendoo_studio.services import native_editor
    from vendoo_studio.services.choice_lists import share

    # The form reads the database and may wait on eBay or Chrome. A plain function runs in
    # FastAPI's threadpool, which keeps those waits from holding up every other request.
    try:
        return share(native_editor.read_form(db, conv_id, marketplace, revision_id))
    except native.NativeMarketplaceError as exc:
        raise _http(exc) from exc


@router.post("/{marketplace}/check")
async def check_result(conv_id: str, marketplace: Marketplace, body: CheckRequest | None = None):
    return await _reply(native.check_result(conv_id, marketplace, body))


@router.post("/{marketplace}/form/refresh")
def refresh_form(conv_id: str, marketplace: Literal["ebay", "poshmark", "mercari", "depop", "etsy"],
                 body: SaveDraftRequest, db: Session = Depends(get_db, scope="function")):
    from vendoo_studio.services import native_editor

    return _sync(native_editor.refresh_form, db, conv_id, marketplace, body.revision_id)


@router.post("/{marketplace}/reset")
def reset(conv_id: str, marketplace: Marketplace, body: ResetRequest, db: Session = Depends(get_db, scope="function")):
    if body.confirm is not True:
        raise HTTPException(400, "Confirm that you checked the marketplace yourself.")
    return _sync(native.reset, conv_id, marketplace, db)


