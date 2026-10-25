"""An item's live marketplace listings, and the seller-confirmed Delist."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.services import marketplace_delist

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
