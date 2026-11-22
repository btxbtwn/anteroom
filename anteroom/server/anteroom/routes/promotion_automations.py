"""Seller-enabled scheduled sharing and automatic offers to likers. No listing, publishing or delisting here."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from anteroom.database import get_db
from anteroom.services import promotion_automations
from anteroom.services.listing_promotions import PromotionError

router = APIRouter(prefix="/api/promotion-automations", tags=["promotions"])
Ids = Annotated[list[Annotated[str, Field(max_length=64)]], Field(max_length=promotion_automations.MAX_LISTED_ITEMS)]


class WaterfallStepBody(BaseModel):
    discount_percent: Annotated[int, Field(strict=True, ge=1, le=99)]
    delay_minutes: Annotated[int, Field(strict=True, ge=0, le=43200)]


class FollowConfigBody(BaseModel):
    reciprocal_24h: bool = False
    new_active_sellers_cap: Annotated[int, Field(strict=True, ge=0, le=promotion_automations.MAX_DAILY_FOLLOWS)] = 0
    unfollow_cap: Annotated[int, Field(strict=True, ge=0, le=promotion_automations.MAX_DAILY_UNFOLLOWS)] = 0
    unsubscribe_live_shows: bool = False


class AutomationBody(BaseModel):
    marketplace: Literal["poshmark", "mercari", "depop"]
    action: Literal["share", "offer", "bundle_offer", "follow"]
    name: Annotated[str, Field(max_length=80)] = ""
    scope: Literal["all", "items"] = "all"
    conversation_ids: Ids = Field(default_factory=list)
    excluded_ids: Ids = Field(default_factory=list)
    min_days_listed: Annotated[int, Field(strict=True, ge=0, le=365)] = 0
    share_order: Literal["newest", "oldest", "random"] | None = None
    speed: Literal["slow", "normal", "fast"] | None = None
    daily_cap: Annotated[int, Field(strict=True, ge=1, le=promotion_automations.MAX_DAILY_SHARES)] | None = None
    times: Annotated[list[Annotated[str, Field(max_length=5)]], Field(max_length=promotion_automations.MAX_TIMES)] = Field(default_factory=list)
    timezone: Annotated[str, Field(max_length=64)] | None = None
    share_modes: list[Literal["self", "party", "community"]] | None = None
    hourly_schedule: dict[str, Literal["none", "low", "medium", "high"]] | None = None
    discount_percent: Annotated[int, Field(strict=True, ge=1, le=99)] | None = None
    minimum_price: Annotated[str, Field(max_length=20)] | None = None
    min_likes: Annotated[int, Field(strict=True, ge=1, le=1000)] | None = None
    shipping_label: Annotated[str, Field(max_length=80)] | None = None
    waterfall_steps: list[WaterfallStepBody] | None = Field(default=None, max_length=5)
    bundle_pause_minutes: Annotated[int, Field(strict=True, ge=1, le=1440)] | None = None
    bundle_min_items: Annotated[int, Field(strict=True, ge=1, le=20)] | None = None
    bundle_comment: Annotated[str, Field(max_length=500)] | None = None
    follow_config: FollowConfigBody | None = None


class Confirm(BaseModel):
    confirm: Literal[True]


def _error(exc: PromotionError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


@router.get("")
def listing(db: Session = Depends(get_db, scope="function")):
    return promotion_automations.listing(db)


@router.post("")
def create(body: AutomationBody, db: Session = Depends(get_db, scope="function")):
    try:
        return promotion_automations.save(db, body.model_dump())
    except PromotionError as exc:
        raise _error(exc) from exc


@router.put("/{automation_id}")
def update(automation_id: str, body: AutomationBody, db: Session = Depends(get_db, scope="function")):
    try:
        return promotion_automations.save(db, body.model_dump(), automation_id)
    except PromotionError as exc:
        raise _error(exc) from exc


@router.delete("/{automation_id}")
def delete(automation_id: str, db: Session = Depends(get_db, scope="function")):
    try:
        promotion_automations.delete(db, automation_id)
    except PromotionError as exc:
        raise _error(exc) from exc
    return {"ok": True}


@router.post("/{automation_id}/enable")
def enable(automation_id: str, body: Confirm, db: Session = Depends(get_db, scope="function")):
    try:
        return promotion_automations.enable(db, automation_id)
    except PromotionError as exc:
        raise _error(exc) from exc


@router.post("/{automation_id}/pause")
def pause(automation_id: str, db: Session = Depends(get_db, scope="function")):
    promotion_automations.pause(db, automation_id)
    return {"ok": True}


@router.post("/pause")
def pause_all(db: Session = Depends(get_db, scope="function")):
    return {"paused": promotion_automations.pause(db)}


@router.get("/{automation_id}/coverage")
def coverage(automation_id: str, db: Session = Depends(get_db, scope="function")):
    try:
        return promotion_automations.coverage(db, automation_id)
    except PromotionError as exc:
        raise _error(exc) from exc


@router.get("/{automation_id}/activity")
def activity(automation_id: str, db: Session = Depends(get_db, scope="function")):
    try:
        return promotion_automations.activity(db, automation_id)
    except PromotionError as exc:
        raise _error(exc) from exc
