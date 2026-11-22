"""Bounded photo uploads and authenticated reads; no filesystem-backed routes."""
import asyncio
import hashlib
import logging
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from typing import Literal
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from anteroom.models.conversation import Conversation, Photo
from anteroom.services import hosted_photos as photos
from anteroom.services.hosted_database import get_seller_db, in_seller_session
from anteroom.services.hosted_limits import hold_upload_slot, read_bounded_body

router = APIRouter(prefix='/api/drafts/{draft_id}/photos')
log = logging.getLogger(__name__)


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
        from anteroom.services.background_tasks import require_idle
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
def list_photos(draft_id: str, db=Depends(get_seller_db, scope="function")):
    require_draft(db, draft_id)
    return db.scalars(select(Photo).where(Photo.conversation_id == draft_id)
                      .order_by(Photo.display_order, Photo.id)).all()


def save_photo(db, draft_id, key, data, width, height, marketplace=None):
    require_draft(db, draft_id, lock=True)
    from anteroom.services.marketplace_media import PHOTO_LIMITS
    own = Photo.marketplace.is_(None) if marketplace is None else Photo.marketplace == marketplace
    count = db.scalar(select(func.count()).select_from(Photo).where(Photo.conversation_id == draft_id, own))
    limit = photos.MAX_PHOTOS if marketplace is None else PHOTO_LIMITS[marketplace]
    if count >= limit:
        raise HTTPException(422, f'Add at most {limit} photos on this form')
    # One past the highest position, not the count: after a delete the count
    # can land on or ahead of a photo the seller already has.
    highest = db.scalar(select(func.max(Photo.display_order)).where(Photo.conversation_id == draft_id, own))
    photo = Photo(conversation_id=draft_id, original_filename='photo.jpg', stored_filename=key,
                  mime_type='image/jpeg', size_bytes=len(data), checksum=hashlib.sha256(data).hexdigest(),
                  width=width, height=height, display_order=0 if highest is None else highest + 1,
                  marketplace=marketplace)
    db.add(photo)
    db.commit()
    return PhotoResponse.model_validate(photo)


@router.post('', response_model=PhotoResponse, status_code=201)
async def upload_photo(draft_id: str, request: Request, marketplace: Literal['poshmark', 'mercari', 'depop', 'ebay', 'etsy'] | None = None):
    # Check ownership before reading bytes. Hold one per-seller upload slot
    # across body receive and Pillow normalization so concurrent uploads cannot
    # exhaust Machine RAM or starve other sellers, then release before Storage I/O.
    await in_seller_session(request, require_draft, draft_id)
    async with hold_upload_slot(request, per_seller=True):
        data = await read_bounded_body(
            request,
            photos.MAX_BYTES,
            too_large_detail=f'Choose a photo of {photos.MAX_BYTES // (1024 * 1024)} MB or smaller',
        )
        image, width, height = await asyncio.to_thread(photos.normalize, bytes(data))
        del data
    token = request.headers['authorization'].partition(' ')[2]
    key = photos.new_key(request.state.seller.user_id, draft_id)
    await asyncio.to_thread(photos.storage_call, 'POST', key, token, image)
    try:
        return await in_seller_session(request, save_photo, draft_id, key, image, width, height, marketplace)
    except Exception:
        # A refused or failed save must not leave an unreachable object behind.
        try:
            await asyncio.to_thread(photos.storage_call, 'DELETE', key, token)
        except HTTPException:
            pass
        raise


@router.get('/{photo_id}')
def read_photo(draft_id: str, photo_id: str, request: Request, db=Depends(get_seller_db, scope="function")):
    photo = get_photo(db, draft_id, photo_id)
    key = photo.stored_filename
    db.rollback()
    data = photos.storage_call('GET', key, request.headers['authorization'].partition(' ')[2])
    return Response(data, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})


@router.delete('/{photo_id}', status_code=204)
def delete_photo(draft_id: str, photo_id: str, request: Request, db=Depends(get_seller_db, scope="function")):
    require_draft(db, draft_id, lock=True)
    photo = get_photo(db, draft_id, photo_id)
    keys = {photo.stored_filename}
    if photo.source_filename:
        photos.check_key(photo.source_filename, db.user_id, draft_id)
        keys.add(photo.source_filename)
    from anteroom.services.hosted_listing_contract import remove_photo_selections
    remove_photo_selections(db, draft_id, photo_id)
    db.delete(photo)
    # Commit before Storage, so the draft's row lock is not held while it answers.
    db.commit()
    token = request.headers['authorization'].partition(' ')[2]
    for key in keys:
        try:
            photos.storage_call('DELETE', key, token)
        except HTTPException:
            # The photo is gone from the draft; only an unreachable private object remains.
            log.warning('A deleted photo stayed in Storage')
    return Response(status_code=204)
