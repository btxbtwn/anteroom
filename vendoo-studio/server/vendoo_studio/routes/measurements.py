from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from vendoo_studio.config import MAX_PHOTO_SIZE_MB, PHOTOS_DIR
from vendoo_studio.database import get_db
from vendoo_studio.repositories.queries import ConversationRepo
from vendoo_studio.services import measure_read
from vendoo_studio.services.listing_provider import get_photo_provider

router = APIRouter(tags=["measurements"])

GARMENTS = ("top", "pants", "skirt")


class ReadSavedRequest(BaseModel):
    garment: str


def _provider(garment: str):
    if garment not in GARMENTS:
        raise HTTPException(400, f"Garment must be one of {', '.join(GARMENTS)}")
    provider = get_photo_provider()
    if provider is None or not callable(getattr(provider, "vision_chat", None)):
        raise HTTPException(503, "Connect a model in Settings to read measurements from photos.")
    return provider


async def _read(provider, paths: list[Path], garment: str) -> dict:
    try:
        return await measure_read.read_measurements(provider, paths, garment)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/api/measurements/read")
async def read_measurements(files: list[UploadFile] = File(...), garment: str = Form(...)):
    """Suggest an item's measurements from photos not saved yet (bulk upload). Saves nothing."""
    provider = _provider(garment)
    if len(files) > 20:
        raise HTTPException(400, "Maximum 20 photos per listing")
    with tempfile.TemporaryDirectory(prefix="measure-upload-") as workdir:
        paths = []
        for index, upload in enumerate(files):
            contents = await upload.read()
            if len(contents) > MAX_PHOTO_SIZE_MB * 1024 * 1024:
                raise HTTPException(400, f"{upload.filename or 'Photo'} is over {MAX_PHOTO_SIZE_MB}MB")
            path = Path(workdir) / f"{index:02d}{Path(upload.filename or 'photo.jpg').suffix.lower() or '.jpg'}"
            path.write_bytes(contents)
            paths.append(path)
        return await _read(provider, paths, garment)


@router.post("/api/conversations/{conv_id}/measurements/read")
async def read_saved_measurements(conv_id: str, body: ReadSavedRequest, db: Session = Depends(get_db)):
    """Suggest a saved listing's measurements from its photos, in their order. Saves nothing."""
    provider = _provider(body.garment)
    repo = ConversationRepo(db)
    if not repo.get(conv_id):
        raise HTTPException(404, "Conversation not found")
    paths = [Path(PHOTOS_DIR) / photo.stored_filename for photo in repo.get_photos(conv_id)]
    return await _read(provider, [path for path in paths if path.is_file()], body.garment)
