"""An item's live marketplace listings, and the seller-confirmed Delist."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.services import chrome_sales, facebook_listing, marketplace_delist, sales_ledger

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
def listings(conv_id: str, db: Session = Depends(get_db)):
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


@router.post("/delist")
async def delist(conv_id: str, body: DelistRequest, db: Session = Depends(get_db)):
    try:
        return await marketplace_delist.delist(db, conv_id, body.marketplaces, body.confirm)
    except marketplace_delist.DelistError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.post("/check-sales")
async def check_sales(conv_id: str, db: Session = Depends(get_db)):
    """Read this item's live listings for a sale now. Nothing on any marketplace changes."""
    result = await chrome_sales.check_item(db, conv_id)
    db.expire_all()
    return {**result, "listings": marketplace_delist.listings(db, conv_id)}


@router.post("/facebook/check")
async def check_facebook(conv_id: str, db: Session = Depends(get_db)):
    """Read the linked Facebook listing's state through Chrome. Changes nothing on Facebook."""
    try:
        return await facebook_listing.check(db, conv_id)
    except marketplace_delist.DelistError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.post("/sold")
def record_sale(conv_id: str, body: sales_ledger.SellerSale, db: Session = Depends(get_db)):
    """Mark as sold: record the sale and its amounts. Delisting the rest is the seller's separate, confirmed step."""
    try:
        return sales_ledger.record_seller_sale(db, conv_id, body)
    except sales_ledger.LedgerError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except marketplace_delist.DelistError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
