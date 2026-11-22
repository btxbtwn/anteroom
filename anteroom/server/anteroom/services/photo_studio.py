"""AI Studio on one or more listing photos at once, kept non-destructive.

Each photo goes to Photoroom as the seller sees it now (with any crop, marks or
redactions already applied, so a redacted name never leaves Anteroom). The
answer is stored beside the photo's untouched upload and becomes the base its
edits render from, so Reset returns to the original upload.

Nothing here holds a database session across Photoroom or Storage: each
database step runs in its own short session (``run_db``), and every Photoroom
call has a deadline (``services/photoroom.py``). Photos are independent; one
that fails is reported by itself and the others still save.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from io import BytesIO
from typing import Any

from PIL import Image

from anteroom.models.conversation import Photo
from anteroom.repositories.queries import ConversationRepo
from anteroom.services import photo_edits, photoroom
from anteroom.services.photo_edits import PhotoEdits, StudioEdit
from anteroom.services.photo_store import photo_store
from anteroom.services.photoroom import PhotoroomError, StudioOptions

log = logging.getLogger(__name__)

RunDb = Callable[..., Awaitable[Any]]


def _read_photo(db, conv_id: str, photo_id: str) -> tuple[str, str]:
    """The photo's served file and type, after checking the listing is not busy."""
    from anteroom.services import background_tasks
    from anteroom.services.service_context import current

    try:
        if current() is not None:
            background_tasks.check_idle(db, conv_id)
        photo = db.query(Photo).filter(Photo.id == photo_id, Photo.conversation_id == conv_id).first()
        if photo is None:
            raise photo_edits.PhotoNotFoundError("Photo not found")
        if photo.mime_type not in photo_edits.SAVE_FORMATS:
            raise ValueError("This photo format cannot be edited")
        return photo.stored_filename, photo.mime_type
    finally:
        db.rollback()


def _store_result(served: str, mime_type: str, result: bytes, options: StudioOptions) -> str:
    """Save Photoroom's answer in the photo's own format; returns its key."""
    with Image.open(BytesIO(result)) as answer:
        answer.load()
        img = answer.convert("RGBA")
    fmt = photo_edits.SAVE_FORMATS[mime_type]
    lowest_alpha, _highest = img.getchannel("A").getextrema()
    if fmt == "JPEG" or lowest_alpha == 255:
        fill = "#FFFFFF" if options.virtual_model is not None else options.background
        flat = Image.new("RGB", img.size, fill)
        flat.paste(img, mask=img.getchannel("A"))
        out: Image.Image = flat
    else:
        out = img
    buffer = BytesIO()
    out.save(buffer, fmt, **({"quality": 95} if fmt in ("JPEG", "WEBP") else {}))
    key = photo_store().edited_key(served)
    photo_store().put(key, buffer.getvalue())
    return key


def _save(db, conv_id: str, photo_id: str, served: str, key: str, options: StudioOptions) -> Photo:
    """Make ``key`` the photo's AI Studio base, if the photo is still the one sent.

    What was sent already carried the photo's edits, so they start over on the result.
    """
    studio = StudioEdit(key=key, options=options)
    return photo_edits.apply_photo_edit(ConversationRepo(db), conv_id, photo_id,
                                        lambda _current: PhotoEdits(studio=studio), expected_stored=served)


async def _one(run_db: RunDb, conv_id: str, photo_id: str, options: StudioOptions) -> Photo:
    served, mime_type = await run_db(_read_photo, conv_id, photo_id)
    data = await asyncio.to_thread(photo_store().read, served)
    result = await photoroom.edit(data, options)
    del data
    key = await asyncio.to_thread(_store_result, served, mime_type, result, options)
    try:
        return await run_db(_save, conv_id, photo_id, served, key, options)
    except BaseException:
        # The photo changed or the save failed: the unused result must not linger.
        await asyncio.to_thread(photo_edits.cleanup_uploaded, key)
        raise


def _failure(photo_id: str, error: BaseException) -> dict:
    from fastapi import HTTPException

    if isinstance(error, PhotoroomError):
        detail = str(error)
    elif isinstance(error, photo_edits.PhotoNotFoundError):
        detail = "Photo not found"
    elif isinstance(error, photo_edits.PhotoConflictError):
        detail = str(error)
    elif isinstance(error, FileNotFoundError):
        detail = "Photo file not found"
    elif isinstance(error, HTTPException):
        detail = str(error.detail)
    elif isinstance(error, ValueError):
        detail = str(error)
    else:
        log.warning("AI Studio failed for a photo", exc_info=error)
        detail = "AI Studio could not save this photo. Try again."
    return {"photo_id": photo_id, "detail": detail}


async def run(run_db: RunDb, conv_id: str, photo_ids: list[str], options: StudioOptions) -> dict:
    """Run AI Studio on each photo, a few at a time; returns saved ``Photo`` rows and per-photo failures."""
    if not photo_ids:
        raise ValueError("Choose at least one photo.")
    if len(photo_ids) > photoroom.MAX_BATCH:
        raise ValueError(f"Choose at most {photoroom.MAX_BATCH} photos at once.")
    if len(set(photo_ids)) != len(photo_ids):
        raise ValueError("Choose each photo once.")
    if not photoroom.available():
        raise PhotoroomError("Add a Photoroom API key in Settings → Connections to use AI Studio.", 400)
    slots = asyncio.Semaphore(photoroom.CONCURRENCY)

    async def bounded(photo_id: str) -> Photo:
        async with slots:
            return await _one(run_db, conv_id, photo_id, options)

    outcomes = await asyncio.gather(*(bounded(photo_id) for photo_id in photo_ids), return_exceptions=True)
    saved, failed = [], []
    for photo_id, outcome in zip(photo_ids, outcomes, strict=True):
        if isinstance(outcome, asyncio.CancelledError):
            raise outcome
        if isinstance(outcome, BaseException):
            failed.append(_failure(photo_id, outcome))
        else:
            saved.append(outcome)
    return {"photos": saved, "errors": failed}
