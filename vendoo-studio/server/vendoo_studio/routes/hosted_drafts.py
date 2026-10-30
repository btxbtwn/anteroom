"""Authenticated draft shells; never mounts desktop routes or services."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from vendoo_studio.repositories import hosted_drafts as drafts
from vendoo_studio.services.hosted_database import get_seller_db

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


class DraftResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str | None
    notes: str | None
    created_at: datetime | None
    updated_at: datetime | None


@router.get('', response_model=list[DraftResponse])
def list_drafts(db=Depends(get_seller_db)):
    return drafts.recent(db)


@router.post('', response_model=DraftResponse, status_code=201)
def create_draft(body: DraftInput, db=Depends(get_seller_db)):
    return drafts.create(db, body.title, body.notes)


@router.get('/{draft_id}', response_model=DraftResponse)
def get_draft(draft_id: str, db=Depends(get_seller_db)):
    draft = drafts.get(db, draft_id)
    if draft is None:
        raise HTTPException(404, 'Draft not found')
    return draft


@router.patch('/{draft_id}', response_model=DraftResponse)
def save_draft(draft_id: str, body: DraftSave, db=Depends(get_seller_db)):
    if drafts.get(db, draft_id) is None:
        raise HTTPException(404, 'Draft not found')
    draft = drafts.save(db, draft_id, body.title, body.notes, body.expected_updated_at)
    if draft is None:
        raise HTTPException(409, 'This draft changed on another device. Reload it before saving again.')
    return draft
