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


@router.get("")
def listings(conv_id: str, db: Session = Depends(get_db)):
    try:
        return marketplace_delist.listings(db, conv_id)
    except marketplace_delist.DelistError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.post("/delist")
async def delist(conv_id: str, body: DelistRequest, db: Session = Depends(get_db)):
    try:
        return await marketplace_delist.delist(db, conv_id, body.marketplaces, body.confirm)
    except marketplace_delist.DelistError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
