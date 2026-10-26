"""Direct marketplace List, Update, Check, Reset and Cancel endpoints."""
from __future__ import annotations

from collections.abc import Awaitable
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.services import native_marketplaces as native
from vendoo_studio.services.native_marketplaces import CheckRequest, InspectionMarketplace, InspectRequest, ListRequest, Marketplace

router = APIRouter(prefix="/api/conversations/{conv_id}/native-marketplaces", tags=["native-marketplaces"])


class ResetRequest(BaseModel):
    """The seller confirms they looked on the marketplace themselves."""
    confirm: bool = False


async def _reply(call: Awaitable[dict]) -> dict:
    try:
        return await call
    except native.NativeMarketplaceError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


def _sync(call, *args):
    try:
        return call(*args)
    except native.NativeMarketplaceError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("")
def status(conv_id: str, db: Session = Depends(get_db)):
    return _sync(native.status, conv_id, db)


@router.post("/sync")
async def sync(conv_id: str, db: Session = Depends(get_db)):
    """Check linked marketplace inventory, settle results, and look for sales."""
    return await _reply(native.sync(conv_id, db))


@router.get("/preflight")
async def preflight(conv_id: str, sellwild_category_id: int | None = Query(None, ge=1),
                    sellwild_condition_id: int | None = Query(None, ge=1), db: Session = Depends(get_db)):
    """What each marketplace still needs before List."""
    return await _reply(native.preflight(conv_id, db, sellwild_category_id, sellwild_condition_id))


@router.post("/{marketplace}/inspect")
async def inspect_page(conv_id: str, marketplace: InspectionMarketplace, body: InspectRequest, db: Session = Depends(get_db)):
    return await _reply(native.inspect_page(conv_id, marketplace, body, db))


@router.post("/{marketplace}/list")
async def list_now(conv_id: str, marketplace: Marketplace, body: ListRequest, db: Session = Depends(get_db)):
    return await _reply(native.list_now(conv_id, marketplace, body, db))


@router.post("/{marketplace}/update")
async def update_live(conv_id: str, marketplace: Marketplace, body: ListRequest, db: Session = Depends(get_db)):
    return await _reply(native.update_live(conv_id, marketplace, body, db))


@router.post("/{marketplace}/cancel")
async def cancel(conv_id: str, marketplace: Marketplace, db: Session = Depends(get_db)):
    return await _reply(native.cancel(conv_id, marketplace, db))


@router.get("/{marketplace}/form")
async def editor_form(conv_id: str, marketplace: Literal["ebay", "poshmark", "mercari", "depop", "sellwild"],
                      revision_id: str | None = None, db: Session = Depends(get_db)):
    from vendoo_studio.services.native_editor import editor_form as load_form

    return await _reply(load_form(conv_id, marketplace, revision_id, db))


@router.get("/sellwild/options")
def sellwild_options(conv_id: str, db: Session = Depends(get_db)):
    return _sync(native.sellwild_options, conv_id, db)


@router.post("/{marketplace}/check")
async def check_result(conv_id: str, marketplace: Marketplace, body: CheckRequest | None = None, db: Session = Depends(get_db)):
    return await _reply(native.check_result(conv_id, marketplace, db, body))


@router.post("/{marketplace}/reset")
def reset(conv_id: str, marketplace: Marketplace, body: ResetRequest, db: Session = Depends(get_db)):
    if body.confirm is not True:
        raise HTTPException(400, "Confirm that you checked the marketplace yourself.")
    return _sync(native.reset, conv_id, marketplace, db)
