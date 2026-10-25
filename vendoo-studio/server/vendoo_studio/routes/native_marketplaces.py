"""Native marketplace draft and seller-confirmed publish endpoints."""
from __future__ import annotations

from collections.abc import Awaitable

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.services import native_marketplaces as native
from vendoo_studio.services.native_marketplaces import CheckRequest, DraftRequest, InspectionMarketplace, InspectRequest, Marketplace, PublishRequest

router = APIRouter(prefix="/api/conversations/{conv_id}/native-marketplaces", tags=["native-marketplaces"])


async def _reply(call: Awaitable[dict]) -> dict:
    try:
        return await call
    except native.NativeMarketplaceError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("")
def status(conv_id: str, db: Session = Depends(get_db)):
    try:
        return native.status(conv_id, db)
    except native.NativeMarketplaceError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.post("/{marketplace}/draft")
async def save_draft(conv_id: str, marketplace: Marketplace, body: DraftRequest, db: Session = Depends(get_db)):
    return await _reply(native.save_draft(conv_id, marketplace, body, db))


@router.post("/{marketplace}/inspect")
async def inspect_page(conv_id: str, marketplace: InspectionMarketplace, body: InspectRequest, db: Session = Depends(get_db)):
    return await _reply(native.inspect_page(conv_id, marketplace, body, db))


@router.post("/{marketplace}/list")
async def list_now(conv_id: str, marketplace: Marketplace, body: PublishRequest, db: Session = Depends(get_db)):
    return await _reply(native.list_now(conv_id, marketplace, body, db))


@router.post("/{marketplace}/update")
async def update_live(conv_id: str, marketplace: Marketplace, body: PublishRequest, db: Session = Depends(get_db)):
    return await _reply(native.update_live(conv_id, marketplace, body, db))


@router.post("/{marketplace}/publish")
async def publish(conv_id: str, marketplace: Marketplace, body: PublishRequest, db: Session = Depends(get_db)):
    return await _reply(native.publish(conv_id, marketplace, body, db))


@router.get("/sellwild/options")
def sellwild_options(conv_id: str, db: Session = Depends(get_db)):
    try:
        return native.sellwild_options(conv_id, db)
    except native.NativeMarketplaceError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.post("/{marketplace}/check")
async def check_result(conv_id: str, marketplace: Marketplace, body: CheckRequest | None = None, db: Session = Depends(get_db)):
    return await _reply(native.check_result(conv_id, marketplace, db, body))
