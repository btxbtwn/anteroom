"""Reviewed account price changes. These handlers never contact a marketplace."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from vendoo_studio.models.conversation import Conversation
from vendoo_studio.services import price_markdown
from vendoo_studio.services.hosted_database import get_seller_db

router = APIRouter(prefix='/api/price-drop/batch', tags=['inventory'])


class PreviewInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    conversation_ids: list[str] = Field(min_length=1, max_length=300)


class ReviewedDrop(BaseModel):
    model_config = ConfigDict(extra='forbid')
    conversation_id: str
    price: float = Field(gt=0, le=1000000, allow_inf_nan=False)
    expected_revision_id: str = Field(min_length=1)


class ApplyInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    drops: list[ReviewedDrop] = Field(min_length=1, max_length=300)


def require_owned(db, identities):
    wanted = set(identities)
    owned = set(db.scalars(select(Conversation.id).where(Conversation.id.in_(wanted))))
    if wanted != owned:
        raise HTTPException(404, 'Listing not found')


@router.post('/preview')
def preview(body: PreviewInput, db=Depends(get_seller_db)):
    require_owned(db, body.conversation_ids)
    return {'rows': price_markdown.batch_preview(body.conversation_ids, db)}


@router.post('')
def apply(body: ApplyInput, db=Depends(get_seller_db)):
    # Check every owner before any item can be changed.
    require_owned(db, [drop.conversation_id for drop in body.drops])
    return price_markdown.batch_apply([drop.model_dump() for drop in body.drops], db)
