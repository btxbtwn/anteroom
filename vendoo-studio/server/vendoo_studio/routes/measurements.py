from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from vendoo_studio.services import measure_read
from vendoo_studio.services.listing_provider import get_photo_provider
from vendoo_studio.config import MAX_PHOTO_SIZE_MB

router = APIRouter(prefix="/api/measurements", tags=["measurements"])

GARMENTS = ("top", "pants", "skirt")


@router.post("/read")
async def read_measurements(files: list[UploadFile] = File(...), garment: str = Form(...)):
    """Suggest an item's measurements from its yardstick photos. Saves nothing."""
    if garment not in GARMENTS:
        raise HTTPException(400, f"Garment must be one of {', '.join(GARMENTS)}")
    if len(files) > 20:
        raise HTTPException(400, "Maximum 20 photos per listing")
    provider = get_photo_provider()
    if provider is None or not callable(getattr(provider, "vision_chat", None)):
        raise HTTPException(503, "Connect a model in Settings to read measurements from photos.")
    with tempfile.TemporaryDirectory(prefix="measure-upload-") as workdir:
        paths = []
        for index, upload in enumerate(files):
            contents = await upload.read()
            if len(contents) > MAX_PHOTO_SIZE_MB * 1024 * 1024:
                raise HTTPException(400, f"{upload.filename or 'Photo'} is over {MAX_PHOTO_SIZE_MB}MB")
            path = Path(workdir) / f"{index:02d}{Path(upload.filename or 'photo.jpg').suffix.lower() or '.jpg'}"
            path.write_bytes(contents)
            paths.append(path)
        try:
            return await measure_read.read_measurements(provider, paths, garment)
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc
