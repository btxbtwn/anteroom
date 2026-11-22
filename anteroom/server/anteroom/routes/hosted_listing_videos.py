"""Listing videos on the web: upload to private Storage, play through a short-lived link.

The same paths and answers as the Mac's ``routes/listing_videos.py``. A video is
only ever stored and previewed here; it reaches a marketplace when the seller
lists or updates with it selected on that marketplace's form.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from anteroom.models.conversation import Conversation
from anteroom.models.listing_video import ListingVideo
from anteroom.services import hosted_videos, listing_videos as videos
from anteroom.services.hosted_database import get_seller_db, in_seller_session
from anteroom.services.hosted_limits import bounded_route_class, release_upload_slot, stream_upload_to_path

# A multipart form around the file adds a little.
_MAX_BODY = hosted_videos.MAX_BYTES + 1024 * 1024
TOO_LARGE = 'Videos must be 150 MB or smaller (the most any marketplace takes).'

router = APIRouter(tags=['listing-videos'])
upload_router = APIRouter(tags=['listing-videos'], route_class=bounded_route_class(
    _MAX_BODY, too_large_detail=TOO_LARGE, use_upload_slots=True))


def _conversation(db, conv_id: str) -> None:
    if db.get(Conversation, conv_id) is None:
        raise HTTPException(404, 'Conversation not found')


@router.get('/api/conversations/{conv_id}/videos')
def library(conv_id: str, db=Depends(get_seller_db, scope='function')):
    _conversation(db, conv_id)
    rows = db.query(ListingVideo).filter_by(conversation_id=conv_id).order_by(ListingVideo.created_at, ListingVideo.id).all()
    return {'videos': [videos.video_view(row) for row in rows], 'max_bytes': hosted_videos.MAX_BYTES,
            'rules': videos.VIDEO_RULES}


def _insert(db, conv_id: str, key: str, checksum: str, filename: str, size: int, metadata: dict) -> dict:
    _conversation(db, conv_id)
    row = ListingVideo(conversation_id=conv_id, stored_filename=key, checksum=checksum, original_filename=filename,
                       size_bytes=size, **metadata)
    db.add(row)
    db.commit()
    db.refresh(row)
    return videos.video_view(row)


def _measure(path: Path) -> tuple[str, dict]:
    import hashlib

    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest(), videos.read_video_metadata(path)


@upload_router.post('/api/conversations/{conv_id}/videos')
async def upload(conv_id: str, request: Request, file: UploadFile = File(...)):
    from anteroom.services.service_context import current

    await in_seller_session(request, _conversation, conv_id)
    context = current()
    assert context is not None
    with tempfile.TemporaryDirectory(prefix='anteroom-video-') as folder:
        path = Path(folder) / 'upload.video'
        size = await stream_upload_to_path(file, path, hosted_videos.MAX_BYTES, too_large_detail=TOO_LARGE)
        try:
            checksum, metadata = await asyncio.to_thread(_measure, path)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        # The body is in; Storage and the database need no upload slot.
        await release_upload_slot(request)
        key = hosted_videos.new_key(context.seller_id, conv_id)
        await asyncio.to_thread(hosted_videos.upload, key, path, metadata['mime_type'])
    name = Path(file.filename or 'video').name[:200]
    try:
        return await in_seller_session(request, _insert, conv_id, key, checksum, name, size, metadata)
    except BaseException:
        # A refused save must not leave an unreachable object behind.
        try:
            await asyncio.to_thread(hosted_videos.delete, key)
        except HTTPException:
            pass
        raise


def _key(db, video_id: str) -> str:
    row = db.get(ListingVideo, video_id)
    if row is None:
        raise HTTPException(404, 'Video not found')
    return row.stored_filename


@router.get('/api/videos/{video_id}/link')
async def link(video_id: str, request: Request):
    """A short-lived address the web app's player reads the video from."""
    key = await in_seller_session(request, _key, video_id)
    url = await asyncio.to_thread(hosted_videos.signed_url, key)
    return {'url': url, 'expires_in': hosted_videos.LINK_SECONDS}


def _remove(db, conv_id: str, video_id: str) -> str:
    _conversation(db, conv_id)
    row = db.query(ListingVideo).filter_by(id=video_id, conversation_id=conv_id).first()
    if not row:
        raise HTTPException(404, 'Video not found')
    if refusal := videos.deletion_refusal(db, conv_id, video_id):
        raise HTTPException(409, refusal)
    key = row.stored_filename
    db.delete(row)
    db.commit()
    return key


@router.delete('/api/conversations/{conv_id}/videos/{video_id}')
async def delete(conv_id: str, video_id: str, request: Request):
    key = await in_seller_session(request, _remove, conv_id, video_id)
    try:
        await asyncio.to_thread(hosted_videos.delete, key)
    except HTTPException:
        # The video is gone from the listing; only an unreachable private object remains.
        pass
    return {'ok': True}
