from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.services.etsy_audit import etsy_not_allowed_live

router = APIRouter(prefix="/api/etsy", tags=["etsy"])


class EtsyNotAllowedItem(BaseModel):
    conversation_id: str
    title: str
    when_made: str
    who_made: str
    listed_at: str | None = None
    etsy_url: str | None = None
    title_says_vintage: bool = False


class EtsyNotAllowedResponse(BaseModel):
    items: list[EtsyNotAllowedItem]


@router.get("/not-allowed", response_model=EtsyNotAllowedResponse)
def get_not_allowed(db: Session = Depends(get_db)):
    """Read-only: items live on Etsy that Etsy does not allow. Nothing here delists."""
    return EtsyNotAllowedResponse(items=[
        EtsyNotAllowedItem.model_validate(row) for row in etsy_not_allowed_live(db)
    ])
