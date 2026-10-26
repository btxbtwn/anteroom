"""Local original videos, selected explicitly on a marketplace's listing form."""
from __future__ import annotations

import asyncio
import hashlib
import math
from pathlib import Path
import uuid

from fastapi import UploadFile
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from sqlalchemy.orm import Session

from vendoo_studio.config import DATA_DIR
from vendoo_studio.models.listing_video import ListingVideo

VIDEOS_DIR = Path(DATA_DIR) / "videos"
MAX_VIDEO_BYTES = 500 * 1024 * 1024


def video_path(row: ListingVideo) -> Path:
    return VIDEOS_DIR / Path(row.stored_filename).name


def video_view(row: ListingVideo) -> dict:
    return {"id": row.id, "original_filename": row.original_filename, "mime_type": row.mime_type,
            "size_bytes": row.size_bytes, "duration_seconds": row.duration_seconds, "width": row.width,
            "height": row.height, "url": f"/api/videos/{row.id}"}


def read_video_metadata(path: Path) -> dict:
    try:
        parser = createParser(str(path))
        if parser is None:
            raise ValueError("This file is not a supported video.")
        with parser:
            metadata = extractMetadata(parser)
    except Exception as exc:
        raise ValueError("This file could not be read as a supported video.") from exc
    if metadata is None:
        raise ValueError("This video's duration and dimensions could not be read.")
    duration = metadata.get("duration", None)
    duration = duration.total_seconds() if duration is not None else 0
    width, height = metadata.get("width", 0), metadata.get("height", 0)
    mime = metadata.get("mime_type", "")
    if not str(mime).startswith("video/") or not math.isfinite(duration) or duration <= 0 or min(width, height) <= 0:
        raise ValueError("Choose a video with a readable duration and picture dimensions.")
    return {"duration_seconds": duration, "width": width, "height": height, "mime_type": mime}


async def store_upload(db: Session, conv_id: str, upload: UploadFile) -> ListingVideo:
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid.uuid4().hex}.video"
    path = VIDEOS_DIR / stored
    digest, size = hashlib.sha256(), 0
    try:
        with path.open("xb") as stream:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_VIDEO_BYTES:
                    raise ValueError("Videos uploaded to Anteroom must be 500 MB or smaller.")
                digest.update(chunk)
                stream.write(chunk)
        metadata = await asyncio.to_thread(read_video_metadata, path)
        row = ListingVideo(conversation_id=conv_id, stored_filename=stored, checksum=digest.hexdigest(),
                           original_filename=Path(upload.filename or "video").name, size_bytes=size, **metadata)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
    except BaseException:
        db.rollback()
        path.unlink(missing_ok=True)
        raise


def selected_video(db: Session, conv_id: str, listing: dict, marketplace: str) -> dict | None:
    video_id = (listing.get(f"{marketplace}_specifics") or {}).get("videoId")
    if not video_id:
        return None
    row = db.query(ListingVideo).filter_by(id=str(video_id), conversation_id=conv_id).first()
    if row is None or not video_path(row).is_file():
        raise ValueError("The selected listing video is missing. Choose another video or clear it.")
    if marketplace == "poshmark" and row.duration_seconds >= 15:
        raise ValueError("Poshmark's web editor requires a video shorter than 15 seconds.")
    return {**video_view(row), "checksum": row.checksum}
