"""Cached marketplace spreadsheet cells and readiness for the inventory's marketplace tabs."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from anteroom.database import get_db
from anteroom.routes.api_contract import ListingSheetResponse
from anteroom.services import listing_sheet

router = APIRouter(tags=["listing-sheet"])


@router.get("/api/listing-sheet", response_model=ListingSheetResponse)
def sheet(
    marketplace: Literal["ebay", "poshmark", "mercari", "depop", "etsy"] = Query(...),
    ids: list[str] = Query(default=[], max_length=listing_sheet.MAX_LISTINGS),
    db: Session = Depends(get_db, scope="function"),
):
    """Read-only, cached-only. Plain def so FastAPI runs DB and catalog reads in the threadpool."""
    return listing_sheet.for_listings(db, marketplace, ids)
