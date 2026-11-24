"""Listings an import left for the seller to compare with an existing item. Only Anteroom changes here."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from anteroom.database import get_db
from anteroom.services import import_reviews

router = APIRouter(prefix="/api/import-reviews", tags=["import-reviews"])


@router.get("")
def list_import_reviews(marketplace: Literal["ebay", "etsy", "poshmark", "mercari", "depop"] | None = Query(None),
                        db: Session = Depends(get_db, scope="function")):
    return {"reviews": import_reviews.pending(db, marketplace)}


@router.post("/{review_id}/not-a-match")
def not_a_match(review_id: str, db: Session = Depends(get_db, scope="function")):
    """The listing and the suggested item are different products: stop suggesting that item for it."""
    try:
        return import_reviews.reject(db, review_id)
    except import_reviews.ReviewNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
