"""Explicit seller controls for eBay offers; no listing or delisting endpoints."""
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.services import ebay_api, ebay_offers
from vendoo_studio.services.marketplace_accounts import SignInRequired

router = APIRouter(prefix="/api/ebay-offers", tags=["ebay-offers"])


class Selection(BaseModel):
    conversation_id: str
    minimum_price: str = Field(max_length=20)


class PreviewRequest(BaseModel):
    selections: list[Selection] = Field(min_length=1, max_length=50)
    discount_percent: Annotated[int, Field(strict=True, ge=5, le=99)]


class Approval(BaseModel):
    attempt_ids: list[str] = Field(min_length=1, max_length=50)
    confirm: Literal[True]


class Confirm(BaseModel):
    confirm: Literal[True]


class AllListingsApproval(Confirm):
    discount_percent: Annotated[int, Field(strict=True, ge=5, le=99)]
    minimum_price: str | None = Field(default=None, max_length=20)


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, SignInRequired):
        return HTTPException(403, str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(409, str(exc))
    return HTTPException(502, str(exc) if isinstance(exc, ebay_api.EbayError) else "Can't reach eBay. Try again later.")


@router.get("")
def status(db: Session = Depends(get_db)):
    return ebay_offers.status(db)


@router.post("/preview")
async def preview(body: PreviewRequest, db: Session = Depends(get_db)):
    try:
        return await ebay_offers.preview(db, [item.model_dump() for item in body.selections], body.discount_percent)
    except (ValueError, SignInRequired, ebay_api.EbayError, httpx.HTTPError) as exc:
        raise _error(exc) from exc


@router.post("/send")
async def send(body: Approval, db: Session = Depends(get_db)):
    return await ebay_offers.send_previews(db, list(dict.fromkeys(body.attempt_ids)))


@router.post("/enable")
def enable(body: Approval, db: Session = Depends(get_db)):
    try:
        return ebay_offers.enable(db, list(dict.fromkeys(body.attempt_ids)))
    except (ValueError, SignInRequired) as exc:
        raise _error(exc) from exc


@router.post("/pause")
def pause_all(db: Session = Depends(get_db)):
    try:
        return ebay_offers.pause(db)
    except SignInRequired as exc:
        raise _error(exc) from exc


@router.post("/enable-all")
def enable_all(body: AllListingsApproval, db: Session = Depends(get_db)):
    try:
        return ebay_offers.enable_all(db, body.discount_percent, body.minimum_price)
    except (ValueError, SignInRequired) as exc:
        raise _error(exc) from exc


@router.post("/rules/{rule_id}/pause")
def pause(rule_id: str, db: Session = Depends(get_db)):
    try:
        return ebay_offers.pause(db, rule_id)
    except SignInRequired as exc:
        raise _error(exc) from exc


@router.post("/attempts/{attempt_id}/checked")
def resolve(attempt_id: str, body: Confirm, db: Session = Depends(get_db)):
    try:
        return ebay_offers.resolve(db, attempt_id)
    except (ValueError, SignInRequired) as exc:
        raise _error(exc) from exc
