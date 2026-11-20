from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from anteroom.config import MAX_PHOTO_SIZE_MB
from anteroom.database import get_db, in_worker_session
from anteroom.models.scout import ScoutCheck
from anteroom.services import scout
from anteroom.services.hosted_limits import bounded_route_class, read_bounded_upload
from anteroom.services.photo_store import photo_store
from anteroom.services.photos import process_bytes


def _max_scout_body_bytes() -> int:
    # Multipart envelope around at most MAX_PHOTOS images; extra small files still reach the 400 count check.
    return scout.MAX_PHOTOS * MAX_PHOTO_SIZE_MB * 1024 * 1024 + 2 * 1024 * 1024


_scout_router = APIRouter(
    prefix="/api/scout",
    tags=["scout"],
    route_class=bounded_route_class(_max_scout_body_bytes, too_large_detail="Upload is too large"),
)

_upload_router = APIRouter(prefix="/api/scout", tags=["scout"], route_class=bounded_route_class(
    _max_scout_body_bytes, too_large_detail="Upload is too large", use_upload_slots=True,
))


class ScoutCheckResponse(BaseModel):
    id: str
    status: str
    asking_price: float | None
    photo_urls: list[str]
    title: str | None
    estimate: float | None
    comps_count: int | None
    comps: str | None
    error: str | None
    decision: str | None
    conversation_id: str | None
    sold_price: float | None
    created_at: str | None
    net: float | None
    pay_up_to: float | None
    profit: float | None
    verdict: str | None


class ScoutTrackRecord(BaseModel):
    checks: int
    bought: int
    sold: int
    median_ratio: float | None
    close: int


class ScoutResponse(BaseModel):
    checks: list[ScoutCheckResponse]
    track_record: ScoutTrackRecord


class AskingPriceUpdate(BaseModel):
    asking_price: float | None = Field(None, ge=0)


class DecisionRequest(BaseModel):
    decision: str = Field(pattern="^(bought|passed)$")


def _check(db: Session, check_id: str) -> ScoutCheck:
    check = db.get(ScoutCheck, check_id)
    if check is None:
        raise HTTPException(404, "Check not found")
    return check


def _response(db: Session, check: ScoutCheck) -> ScoutCheckResponse:
    return ScoutCheckResponse.model_validate(scout.to_dict(check, scout.outcomes(db).get(check.id)))


@_scout_router.get("", response_model=ScoutResponse)
def list_checks(db: Session = Depends(get_db)):
    sold = scout.outcomes(db)
    return ScoutResponse(
        checks=[ScoutCheckResponse.model_validate(scout.to_dict(c, sold.get(c.id))) for c in scout.recent(db)],
        track_record=ScoutTrackRecord.model_validate(scout.track_record(db, sold)),
    )


@_upload_router.post("", response_model=ScoutCheckResponse)
async def create_check(
    request: Request,
    files: list[UploadFile] = File(...),
    asking_price: float | None = Form(None, ge=0),
):
    if len(files) > scout.MAX_PHOTOS:
        raise HTTPException(400, f"Up to {scout.MAX_PHOTOS} photos per check")
    from anteroom.models.conversation import new_id
    collection_id = 'scout-' + new_id()
    max_photo_bytes = MAX_PHOTO_SIZE_MB * 1024 * 1024
    photos = []
    for upload in files:
        raw = await read_bounded_upload(
            upload,
            max_photo_bytes,
            too_large_detail=f"{upload.filename or 'Photo'}: File too large. Max {MAX_PHOTO_SIZE_MB}MB",
        )
        try:
            # Decoding, hashing and storing a photo run on a worker thread, never the event loop.
            photos.append(await asyncio.to_thread(
                process_bytes, bytes(raw), upload.filename, upload.content_type, collection_id=collection_id,
            ))
        except ValueError as exc:
            raise HTTPException(400, f"{upload.filename or 'Photo'}: {exc}") from exc
        finally:
            del raw
    check_id, response = await in_worker_session(_create, photos, asking_price)
    scout.start(check_id)
    return response


def _create(db: Session, photos: list[dict], asking_price: float | None) -> tuple[str, ScoutCheckResponse]:
    check = scout.create_check(db, photos, asking_price)
    return check.id, _response(db, check)


@_scout_router.get("/{check_id}", response_model=ScoutCheckResponse)
def get_check(check_id: str, db: Session = Depends(get_db)):
    return _response(db, _check(db, check_id))


@_scout_router.patch("/{check_id}", response_model=ScoutCheckResponse)
def update_check(check_id: str, body: AskingPriceUpdate, db: Session = Depends(get_db)):
    check = scout.set_asking_price(db, _check(db, check_id), body.asking_price)
    return _response(db, check)


@_scout_router.post("/{check_id}/decision", response_model=ScoutCheckResponse)
def decide(check_id: str, body: DecisionRequest, db: Session = Depends(get_db)):
    check = _check(db, check_id)
    if check.status != "done" and body.decision == "bought":
        raise HTTPException(409, "Wait for the check to finish")
    return _response(db, scout.decide(db, check, body.decision))


@_scout_router.get("/{check_id}/photos/{index}")
def serve_photo(check_id: str, index: int, db: Session = Depends(get_db, scope="function")):
    check = _check(db, check_id)
    photo_list = list(check.photos or [])
    if not 0 <= index < len(photo_list):
        raise HTTPException(404, "Photo not found")
    photo_key = photo_list[index]
    # Ownership and index were verified above. Release the read transaction before
    # synchronous photo_store() I/O (which may download from Storage in hosted mode).
    db.rollback()
    try:
        path = photo_store().local_path(photo_key)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "Photo not found") from None
    return FileResponse(path)


router = APIRouter()
router.include_router(_scout_router)
router.include_router(_upload_router)
