"""Seller-confirmed shares and offers to likers. No listing, publishing or delisting here."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.services import listing_promotions
from vendoo_studio.services.listing_promotions import PromotionError

router = APIRouter(prefix="/api/promotions", tags=["promotions"])
Marketplace = Literal["poshmark"]


class FloorsRequest(BaseModel):
    conversation_ids: list[str] = Field(min_length=1, max_length=listing_promotions.MAX_ITEMS)
    marketplace: Marketplace


class PreviewRequest(BaseModel):
    conversation_ids: list[str] = Field(min_length=1, max_length=listing_promotions.MAX_ITEMS)
    marketplace: Marketplace
    action: Literal["share", "offer"]
    discount_percent: Annotated[int, Field(strict=True, ge=1, le=99)] | None = None
    floors: dict[str, Annotated[str, Field(max_length=20)]] = Field(default_factory=dict)


class PerformRequest(BaseModel):
    confirm: Literal[True]
    # Offers only: one of the reviewed shipping discount IDs, or "none".
    shipping_discount_id: Annotated[str, Field(max_length=40)] | None = None


class Confirm(BaseModel):
    confirm: Literal[True]


def _error(exc: PromotionError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


@router.get("")
def history(db: Session = Depends(get_db)):
    return {"supported": {name: sorted(actions) for name, actions in listing_promotions.SUPPORTED.items()},
            "history": listing_promotions.history(db)}


@router.post("/floors")
def floors(body: FloorsRequest, db: Session = Depends(get_db)):
    return listing_promotions.floors(db, body.conversation_ids, body.marketplace)


@router.post("/preview")
async def preview(body: PreviewRequest, db: Session = Depends(get_db)):
    try:
        return await listing_promotions.preview(db, body.conversation_ids, body.marketplace, body.action,
                                                body.discount_percent, body.floors)
    except PromotionError as exc:
        raise _error(exc) from exc


@router.post("/{promotion_id}/perform")
async def perform(promotion_id: str, body: PerformRequest, db: Session = Depends(get_db)):
    try:
        return await listing_promotions.perform(db, promotion_id, body.shipping_discount_id)
    except PromotionError as exc:
        raise _error(exc) from exc


@router.post("/{promotion_id}/check")
async def check(promotion_id: str, db: Session = Depends(get_db)):
    try:
        return await listing_promotions.check(db, promotion_id)
    except PromotionError as exc:
        raise _error(exc) from exc


@router.post("/{promotion_id}/checked")
def mark_checked(promotion_id: str, body: Confirm, db: Session = Depends(get_db)):
    try:
        return listing_promotions.mark_checked(db, promotion_id)
    except PromotionError as exc:
        raise _error(exc) from exc
