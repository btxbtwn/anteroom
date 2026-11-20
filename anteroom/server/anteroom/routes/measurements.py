from __future__ import annotations

import asyncio
import logging
import tempfile
from collections.abc import Awaitable
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from anteroom.config import MAX_PHOTO_SIZE_MB
from anteroom.database import in_worker_session
from anteroom.models.conversation import Conversation, Photo
from anteroom.repositories.queries import ConversationRepo
from anteroom.routes.api_contract import SIZE_TAG_READ_ERRORS, SizeTagReadResult
from anteroom.services import hosted_photos, measure_read, size_tag_read
from anteroom.services.hosted_database import SellerDatabaseSession, in_seller_session
from anteroom.services.hosted_limits import bounded_route_class, stream_upload_to_path
from anteroom.services.listing_provider import get_photo_provider
from anteroom.services.photo_store import photo_store

MAX_UPLOAD_FILES = 20
_MULTIPART_OVERHEAD_BYTES = 2 * 1024 * 1024


def _max_batch_bytes() -> int:
    return MAX_UPLOAD_FILES * MAX_PHOTO_SIZE_MB * 1024 * 1024 + _MULTIPART_OVERHEAD_BYTES


_measurement_route = bounded_route_class(
    _max_batch_bytes,
    too_large_detail=f"Upload exceeds maximum size ({MAX_PHOTO_SIZE_MB}MB per photo)",
)
router = APIRouter(tags=["measurements"], route_class=_measurement_route)
hosted_router = APIRouter(tags=["measurements"], route_class=_measurement_route)
# Only multipart uploads occupy admission slots; saved-photo reads can wait
# on a model without preventing this seller from submitting another upload.
_upload_route = bounded_route_class(
    _max_batch_bytes,
    too_large_detail=f"Upload exceeds maximum size ({MAX_PHOTO_SIZE_MB}MB per photo)",
    use_upload_slots=True,
)
_upload_router = APIRouter(route_class=_upload_route)
_hosted_upload_router = APIRouter(route_class=_upload_route)
log = logging.getLogger(__name__)

GARMENTS = ("top", "pants", "skirt")


class ReadSavedRequest(BaseModel):
    garment: str


def _photo_provider(what: str):
    provider = get_photo_provider()
    if provider is None or not callable(getattr(provider, "vision_chat", None)):
        raise HTTPException(503, f"Connect a model in Settings to read {what} from photos.")
    return provider


def _provider(garment: str):
    if garment not in GARMENTS:
        raise HTTPException(400, f"Garment must be one of {', '.join(GARMENTS)}")
    return _photo_provider("measurements")


async def _write_uploads(files: list[UploadFile], workdir: str) -> list[Path]:
    """Photos not saved yet (bulk upload), streamed to `workdir` in their order."""
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(400, f"Maximum {MAX_UPLOAD_FILES} photos per listing")
    max_bytes = MAX_PHOTO_SIZE_MB * 1024 * 1024
    paths = []
    for index, upload in enumerate(files):
        path = Path(workdir) / f"{index:02d}{Path(upload.filename or 'photo.jpg').suffix.lower() or '.jpg'}"
        await stream_upload_to_path(
            upload,
            path,
            max_bytes,
            too_large_detail=f"{upload.filename or 'Photo'} is over {MAX_PHOTO_SIZE_MB}MB",
        )
        paths.append(path)
    return paths


async def _explained(reading: Awaitable[dict], what: str, provider=None) -> dict:
    """A reading's failure as a message the seller can act on.

    A provider that failed or stalled is named (502). Anything else is logged and
    explained (500): an unexplained 500 reads to the client as a restart, and it
    would say "the server restarted" for what was really a failed read.
    """
    try:
        return await reading
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    except Exception as exc:
        log.exception("reading %s from photos failed", what)
        # Name the provider that was reading (most failures here are its replies);
        # only what failed: each client adds its own next step after it.
        from anteroom.services.provider_deadlines import provider_label, unwrap

        who = provider_label(getattr(unwrap(provider), "name", None)) if provider is not None else "Anteroom"
        raise HTTPException(500, f"{who} could not read the {what} in these photos.") from exc


async def _read(provider, paths: list[Path], garment: str) -> dict:
    return await _explained(measure_read.read_measurements(provider, paths, garment), "measurements", provider)


@_upload_router.post("/api/measurements/read")
@_hosted_upload_router.post("/api/measurements/read")
async def read_measurements(request: Request, files: list[UploadFile] = File(...), garment: str = Form(...)):
    """Suggest an item's measurements from photos not saved yet (bulk upload). Saves nothing."""
    provider = _provider(garment)
    with tempfile.TemporaryDirectory(prefix="measure-upload-") as workdir:
        paths = await _write_uploads(files, workdir)
        return await _read(provider, paths, garment)


@_upload_router.post("/api/size-tags/read", response_model=SizeTagReadResult, responses=SIZE_TAG_READ_ERRORS)
@_hosted_upload_router.post("/api/size-tags/read", response_model=SizeTagReadResult, responses=SIZE_TAG_READ_ERRORS)
async def read_size_tags(request: Request, files: list[UploadFile] = File(...)):
    """Suggest an item's clothing size from its size tag in photos not saved yet (bulk upload). Saves nothing."""
    provider = _photo_provider("size tags")
    with tempfile.TemporaryDirectory(prefix="size-tag-upload-") as workdir:
        paths = await _write_uploads(files, workdir)
        return await _explained(size_tag_read.read_size_tag(provider, paths), "size", provider)


@router.post("/api/conversations/{conv_id}/measurements/read")
async def read_saved_measurements(conv_id: str, body: ReadSavedRequest):
    """Suggest a saved listing's measurements from its photos, in their order. Saves nothing."""
    provider = _provider(body.garment)
    filenames = await in_worker_session(_photo_filenames, conv_id)
    paths = await asyncio.to_thread(photo_store().local_paths, filenames)
    return await _read(provider, paths, body.garment)


def _photo_filenames(db: Session, conv_id: str) -> list[str]:
    repo = ConversationRepo(db)
    if not repo.get(conv_id):
        raise HTTPException(404, "Conversation not found")
    return [photo.stored_filename for photo in repo.get_photos(conv_id)]


@hosted_router.post("/api/conversations/{conv_id}/measurements/read")
async def read_hosted_saved_measurements(conv_id: str, body: ReadSavedRequest, request: Request):
    """The hosted read of a saved listing: its photos come from private storage. Saves nothing."""
    provider = _provider(body.garment)
    keys = await in_seller_session(request, _hosted_photo_keys, conv_id)
    token = request.headers['authorization'].partition(' ')[2]
    with tempfile.TemporaryDirectory(prefix='measure-saved-') as workdir:
        paths = []
        for index, key in enumerate(keys):
            path = Path(workdir) / f'{index:02d}.jpg'
            path.write_bytes(await asyncio.to_thread(hosted_photos.storage_call, 'GET', key, token))
            paths.append(path)
        return await _read(provider, paths, body.garment)


def _hosted_photo_keys(db: SellerDatabaseSession, conv_id: str) -> list[str]:
    if db.get(Conversation, conv_id) is None:
        raise HTTPException(404, 'Listing not found')
    rows = db.scalars(select(Photo).where(Photo.conversation_id == conv_id, Photo.marketplace.is_(None))
                      .order_by(Photo.display_order, Photo.id)).all()
    for row in rows:
        hosted_photos.check_key(row.stored_filename, db.user_id, conv_id)
    return [row.stored_filename for row in rows]


router.include_router(_upload_router)
hosted_router.include_router(_hosted_upload_router)
