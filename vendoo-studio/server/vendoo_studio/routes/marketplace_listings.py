"""An item's live marketplace listings, and the seller-confirmed Delist."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db, in_worker_session
from vendoo_studio.services import chrome_sales, marketplace_delist, sales_ledger

router = APIRouter(prefix="/api/conversations/{conv_id}/marketplace-listings", tags=["marketplace-listings"])


class DelistRequest(BaseModel):
    """Marketplaces must be named, and the seller must confirm in the same call."""
    marketplaces: list[str] = Field(min_length=1, max_length=6)
    confirm: bool = False


class MarkRequest(BaseModel):
    """The seller's correction for one marketplace; ``url`` is required to mark it listed."""
    state: str
    url: str | None = Field(default=None, max_length=500)


@router.get("")
def listings(conv_id: str, db: Session = Depends(get_db, scope="function")):
    try:
        return marketplace_delist.listings(db, conv_id)
    except marketplace_delist.DelistError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.post("/{marketplace}/mark")
def mark(conv_id: str, marketplace: str, body: MarkRequest, db: Session = Depends(get_db)):
    try:
        return marketplace_delist.mark(db, conv_id, marketplace, body.state, body.url)
    except marketplace_delist.DelistError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


# Delist and Check sales wait on marketplaces or Chrome. They take no request session;
# each database step runs on a worker thread with a short session of its own.
@router.post("/delist")
async def delist(conv_id: str, body: DelistRequest):
    try:
        return await marketplace_delist.delist(conv_id, body.marketplaces, body.confirm)
    except marketplace_delist.DelistError as exc:
        headers = {"X-Anteroom-Reason": "marketplace_busy"} if str(exc) == "Another automation job is queued or running. Wait for it to finish." else None
        raise HTTPException(exc.status_code, str(exc), headers=headers) from exc


@router.post("/check-sales")
async def check_sales(conv_id: str):
    """Read this item's live listings for a sale now. Nothing on any marketplace changes."""
    result = await chrome_sales.check_item(conv_id)
    return {**result, "listings": await in_worker_session(marketplace_delist.listings, conv_id)}


@router.post("/sold")
def record_sale(conv_id: str, body: sales_ledger.SellerSale, db: Session = Depends(get_db)):
    """Mark as sold: record the sale and its amounts. Delisting the rest is the seller's separate, confirmed step."""
    try:
        return sales_ledger.record_seller_sale(db, conv_id, body)
    except sales_ledger.LedgerError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except marketplace_delist.DelistError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
