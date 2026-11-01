"""Bounded photo uploads and authenticated reads; no filesystem-backed routes."""
import hashlib
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from typing import Literal
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from vendoo_studio.models.conversation import Conversation, Photo
from vendoo_studio.services import hosted_photos as photos
from vendoo_studio.services.hosted_database import get_seller_db

router = APIRouter(prefix='/api/drafts/{draft_id}/photos')


class PhotoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    width: int | None
    height: int | None
    size_bytes: int


def require_draft(db, draft_id, *, lock=False):
    statement = select(Conversation).where(Conversation.id == draft_id, Conversation.status == 'draft')
    if lock:
        statement = statement.with_for_update()
    draft = db.scalar(statement)
    if draft is None:
        raise HTTPException(404, 'Draft not found')
    if lock:
        from vendoo_studio.services.background_tasks import require_idle
        require_idle(db, draft_id)
    return draft


def get_photo(db, draft_id, photo_id):
    require_draft(db, draft_id)
    photo = db.scalar(select(Photo).where(Photo.id == photo_id, Photo.conversation_id == draft_id))
    if photo is None:
        raise HTTPException(404, 'Photo not found')
    photos.check_key(photo.stored_filename, db.user_id, draft_id)
    return photo


@router.get('', response_model=list[PhotoResponse])
def list_photos(draft_id: str, db=Depends(get_seller_db)):
    require_draft(db, draft_id)
    return db.scalars(select(Photo).where(Photo.conversation_id == draft_id)
                      .order_by(Photo.display_order, Photo.id)).all()


def upload(db, draft_id, token, data, marketplace=None):
    data, width, height = photos.normalize(data)
    require_draft(db, draft_id, lock=True)
    from vendoo_studio.services.marketplace_media import PHOTO_LIMITS
    own = Photo.marketplace.is_(None) if marketplace is None else Photo.marketplace == marketplace
    count = db.scalar(select(func.count()).select_from(Photo).where(Photo.conversation_id == draft_id, own))
    limit = photos.MAX_PHOTOS if marketplace is None else PHOTO_LIMITS[marketplace]
    if count >= limit:
        raise HTTPException(422, f'Add at most {limit} photos on this form')
    key = photos.new_key(db.user_id, draft_id)
    photo = Photo(conversation_id=draft_id, original_filename='photo.jpg', stored_filename=key,
                  mime_type='image/jpeg', size_bytes=len(data), checksum=hashlib.sha256(data).hexdigest(),
                  width=width, height=height, display_order=count, marketplace=marketplace)
    db.add(photo)
    db.flush()
    photos.storage_call('POST', key, token, data)
    try:
        db.commit()
    except Exception:
        db.rollback()
        # A failed metadata commit must not leave a visible photo in the draft.
        # Best-effort cleanup; the original failure remains sanitized by the DB dependency.
        try:
            photos.storage_call('DELETE', key, token)
        except HTTPException:
            pass
        raise
    db.refresh(photo)
    return PhotoResponse.model_validate(photo)


@router.post('', response_model=PhotoResponse, status_code=201)
async def upload_photo(draft_id: str, request: Request, marketplace: Literal['poshmark', 'mercari', 'depop', 'ebay', 'etsy', 'vinted', 'sellwild'] | None = None, db=Depends(get_seller_db)):
    # Check ownership before reading bytes. Read a raw body, avoiding an unbounded
    # multipart spool before the handler has an opportunity to enforce the limit.
    await run_in_threadpool(require_draft, db, draft_id)
    # The body and decoder can take time; retain no pooled transaction while
    # waiting. Upload checks and locks the conversation again before saving.
    await run_in_threadpool(db.rollback)
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > photos.MAX_BYTES:
            raise HTTPException(413, f'Choose a photo of {photos.MAX_BYTES // (1024 * 1024)} MB or smaller')
        data.extend(chunk)
    token = request.headers['authorization'].partition(' ')[2]
    return await run_in_threadpool(upload, db, draft_id, token, bytes(data), marketplace)


@router.get('/{photo_id}')
def read_photo(draft_id: str, photo_id: str, request: Request, db=Depends(get_seller_db)):
    photo = get_photo(db, draft_id, photo_id)
    key = photo.stored_filename
    db.rollback()
    data = photos.storage_call('GET', key, request.headers['authorization'].partition(' ')[2])
    return Response(data, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})


@router.delete('/{photo_id}', status_code=204)
def delete_photo(draft_id: str, photo_id: str, request: Request, db=Depends(get_seller_db)):
    require_draft(db, draft_id, lock=True)
    photo = get_photo(db, draft_id, photo_id)
    photos.storage_call('DELETE', photo.stored_filename, request.headers['authorization'].partition(' ')[2])
    from vendoo_studio.services.hosted_listing_contract import remove_photo_selections
    remove_photo_selections(db, draft_id, photo_id)
    db.delete(photo)
    db.commit()
    return Response(status_code=204)
