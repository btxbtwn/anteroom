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

from fastapi import HTTPException
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from anteroom.models.conversation import Conversation, Photo
from anteroom.repositories.queries import ConversationRepo
from anteroom.services import item_status, photo_edits, photoroom
from anteroom.services.media_handoff import finish_media_handoff
from anteroom.services.photo_edits import PhotoEdits, StudioEdit
from anteroom.services.photo_store import photo_store
from anteroom.services.photoroom import PhotoroomError, StudioOptions
from anteroom.services.vendoo_import import parse_notes

log = logging.getLogger(__name__)

RunDb = Callable[..., Awaitable[Any]]


def _require_reviewed_draft(
    db: Session, conv_id: str, scheduled_confirmed_at: str | None, *, lock: bool = False,
) -> None:
    from anteroom.models.listing import Listing

    draft = select(Conversation.status, Conversation.notes).where(Conversation.id == conv_id)
    row = db.execute(draft.with_for_update() if lock else draft).first()
    if row is None or row.status != "draft" or item_status.status_for(db, conv_id, parse_notes(row.notes)).status != "draft":
        raise photo_edits.PhotoConflictError("Bulk AI Studio only edits drafts.")

    schedule = select(Listing.scheduled_status, Listing.scheduled_confirmed_at).where(Listing.conversation_id == conv_id)
    pending = db.execute(schedule.with_for_update() if lock else schedule).first()
    if pending is None:
        return
    if pending.scheduled_status == "publishing":
        raise photo_edits.PhotoConflictError("This draft is publishing.")
    if pending.scheduled_status == "scheduled":
        confirmed = pending.scheduled_confirmed_at
        if confirmed is None or confirmed.isoformat() + "Z" != scheduled_confirmed_at:
            raise photo_edits.PhotoConflictError("Publishing schedule changed. Review this draft again.")


def _read_photo(
    db, conv_id: str, photo_id: str, reviewed_version: str | None, scheduled_confirmed_at: str | None,
) -> tuple[str, str]:
    """The photo's served file and type, after checking the listing is not busy."""
    from anteroom.services import background_tasks
    from anteroom.services.service_context import current

    try:
        if current() is not None:
            background_tasks.check_idle(db, conv_id)
        if reviewed_version is not None:
            _require_reviewed_draft(db, conv_id, scheduled_confirmed_at)
        photo = db.query(Photo).filter(Photo.id == photo_id, Photo.conversation_id == conv_id).first()
        if photo is None:
            raise photo_edits.PhotoNotFoundError("Photo not found")
        if reviewed_version is not None and (photo.marketplace is not None or (photo.checksum or "")[:12] != reviewed_version):
            raise photo_edits.PhotoConflictError("Photo changed. Review this draft again.")
        if photo.mime_type not in photo_edits.SAVE_FORMATS:
            raise ValueError("This photo format cannot be edited")
        return photo.stored_filename, photo.mime_type
    finally:
        db.rollback()


def _store_result(key: str, mime_type: str, result: bytes, options: StudioOptions) -> None:
    """Save Photoroom's answer in the photo's own format."""
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
    photo_store().put(key, buffer.getvalue())


def _save(
    db, conv_id: str, photo_id: str, served: str, key: str, options: StudioOptions,
    reviewed_version: str | None, scheduled_confirmed_at: str | None,
) -> Photo:
    """Make ``key`` the photo's AI Studio base, if the photo is still the one sent.

    What was sent already carried the photo's edits, so they start over on the result.
    """
    studio = StudioEdit(key=key, options=options)
    return photo_edits.apply_photo_edit(
        ConversationRepo(db), conv_id, photo_id, lambda _current: PhotoEdits(studio=studio), expected_stored=served,
        validate_save=(lambda: _require_reviewed_draft(db, conv_id, scheduled_confirmed_at, lock=True))
        if reviewed_version is not None else None,
    )


async def _one(
    run_db: RunDb, conv_id: str, photo_id: str, options: StudioOptions,
    reviewed_version: str | None, scheduled_confirmed_at: str | None,
) -> Photo:
    from anteroom.services import subscriptions

    served, mime_type = await run_db(_read_photo, conv_id, photo_id, reviewed_version, scheduled_confirmed_at)
    data = await asyncio.to_thread(photo_store().read, served)
    charged, charged_period = await run_db(subscriptions.reserve_photo_credit, conv_id, photo_id, options)
    try:
        result = await photoroom.edit(data, options)
    except Exception:
        # Only refund provider/network errors; client disconnect (CancelledError) after
        # dispatching to Photoroom can still incur provider billing.
        if charged > 0:
            try:
                await asyncio.shield(
                    run_db(subscriptions.refund_photo_credit, conv_id, photo_id, charged, charged_period)
                )
            except Exception:  # noqa: BLE001 - refund failure must not mask the primary error
                log.warning("Could not refund AI Studio credits for photo %s", photo_id)
        raise
    del data
    key = photo_store().edited_key(served)

    async def persist() -> Photo:
        try:
            await asyncio.to_thread(_store_result, key, mime_type, result, options)
            return await run_db(_save, conv_id, photo_id, served, key, options, reviewed_version, scheduled_confirmed_at)
        except Exception:
            # A refresh or acknowledgement can fail after commit. Read through
            # a fresh session; if ownership is unknown, keep the object.
            if not await run_db(_uses_result, conv_id, photo_id, key):
                await asyncio.to_thread(photo_edits.cleanup_uploaded, key)
            raise

    return await finish_media_handoff(persist())


def _uses_result(db, conv_id: str, photo_id: str, key: str) -> bool:
    row = db.query(Photo.edits).filter_by(conversation_id=conv_id, id=photo_id).first()
    return row is not None and photo_edits.studio_key(row.edits) == key


def _failure(photo_id: str, error: BaseException) -> dict:
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
    needs_review = isinstance(error, (photo_edits.PhotoNotFoundError, photo_edits.PhotoConflictError))
    return {"photo_id": photo_id, "detail": detail, "needs_review": needs_review}


async def run(
    run_db: RunDb, conv_id: str, photo_ids: list[str], options: StudioOptions, *,
    photo_versions: dict[str, str] | None = None, scheduled_confirmed_at: str | None = None,
) -> dict:
    """Run AI Studio on each photo, a few at a time; returns saved ``Photo`` rows and per-photo failures."""
    from anteroom.services import subscriptions

    if not photo_ids:
        raise ValueError("Choose at least one photo.")
    if len(photo_ids) > photoroom.MAX_BATCH:
        raise ValueError(f"Choose at most {photoroom.MAX_BATCH} photos at once.")
    if len(set(photo_ids)) != len(photo_ids):
        raise ValueError("Choose each photo once.")
    if photo_versions is not None and set(photo_versions) != set(photo_ids):
        raise ValueError("Review each selected photo before applying.")
    if not await asyncio.to_thread(photoroom.available):
        raise PhotoroomError("Add a Photoroom API key in Settings → Connections to use AI Studio.", 400)
    await run_db(subscriptions.check_photo_batch, len(photo_ids), options)
    slots = asyncio.Semaphore(photoroom.CONCURRENCY)
    blocked: PhotoroomError | None = None
    plan_exhausted: subscriptions.PlanLimitError | None = None

    async def bounded(photo_id: str) -> Photo:
        nonlocal blocked, plan_exhausted
        async with slots:
            if plan_exhausted is not None:
                raise plan_exhausted
            if blocked is not None:
                raise PhotoroomError("This photo was not sent. Check your Photoroom plan or API key.", 400)
            try:
                return await _one(run_db, conv_id, photo_id, options,
                                  photo_versions[photo_id] if photo_versions is not None else None, scheduled_confirmed_at)
            except subscriptions.PlanLimitError as exc:
                plan_exhausted = exc
                raise
            except PhotoroomError as exc:
                if photo_versions is not None and exc.status == 400:
                    blocked = exc
                raise

    outcomes = await asyncio.gather(*(bounded(photo_id) for photo_id in photo_ids), return_exceptions=True)
    saved, failed = [], []
    for photo_id, outcome in zip(photo_ids, outcomes, strict=True):
        if isinstance(outcome, asyncio.CancelledError):
            raise outcome
        if isinstance(outcome, BaseException):
            failed.append(_failure(photo_id, outcome))
        else:
            saved.append(outcome)
    return {"photos": saved, "errors": failed, "provider_blocked": blocked is not None or plan_exhausted is not None}
