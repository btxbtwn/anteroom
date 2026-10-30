"""Authenticated draft inventory; never mounts desktop services or marketplace actions."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from vendoo_studio.repositories import hosted_drafts as drafts
from vendoo_studio.services.hosted_database import get_seller_db
from vendoo_studio.services.hosted_fields import DraftFields

router = APIRouter(prefix='/api/drafts')


class DraftInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    title: str = Field(min_length=1, max_length=200)
    notes: str = Field(max_length=20000, default='')

    @field_validator('title')
    @classmethod
    def nonblank_title(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Enter a title')
        return value


class DraftSave(DraftInput):
    # JSON carries timestamps as strings, so this field accepts ISO datetimes.
    expected_updated_at: datetime = Field(strict=False)
    fields: DraftFields | None = None


class DraftResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str | None
    notes: str | None
    created_at: datetime | None
    updated_at: datetime | None


@router.get('', response_model=list[DraftResponse])
def list_drafts(offset: int = Query(0, ge=0, le=1000000), search: str = Query('', max_length=200), db=Depends(get_seller_db)):
    return drafts.recent(db, offset, search)


@router.post('', response_model=DraftResponse, status_code=201)
def create_draft(body: DraftInput, db=Depends(get_seller_db)):
    return drafts.create(db, body.title, body.notes)


class DraftDetail(DraftResponse):
    fields: DraftFields


def detail(db, draft):
    return DraftDetail(**DraftResponse.model_validate(draft).model_dump(), fields=drafts.editable_fields(db, draft.id))


@router.get('/{draft_id}', response_model=DraftDetail)
def get_draft(draft_id: str, db=Depends(get_seller_db)):
    draft = drafts.get(db, draft_id)
    if draft is None:
        raise HTTPException(404, 'Draft not found')
    return detail(db, draft)


@router.patch('/{draft_id}', response_model=DraftDetail)
def save_draft(draft_id: str, body: DraftSave, db=Depends(get_seller_db)):
    if drafts.get(db, draft_id) is None:
        raise HTTPException(404, 'Draft not found')
    draft = drafts.save(db, draft_id, body.title, body.notes, body.expected_updated_at, body.fields)
    if draft is None:
        raise HTTPException(409, 'This draft changed on another device. Reload it before saving again.')
    return detail(db, draft)
