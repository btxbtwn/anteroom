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
# What each marketplace accepts, from its own documentation or web client (see
# docs/direct-form-parity.md). ``shorter_than`` is exclusive, ``max_seconds`` inclusive.
VIDEO_RULES: dict[str, dict] = {
    "poshmark": {"name": "Poshmark", "shorter_than": 15},
    # Media API createVideo: at most 157,286,400 bytes. eBay's seller help names MP4 and MOV.
    "ebay": {"name": "eBay", "max_bytes": 157_286_400, "size_label": "150 MB", "mime_types": ["video/mp4", "video/quicktime"]},
    # Etsy Help: 3 to 15 seconds and 100 MB. The upload passes through the relay,
    # a Cloudflare Worker whose request limit is 100 MB on Free and Pro plans, so the file leaves room for the form around it.
    "etsy": {"name": "Etsy", "min_seconds": 3, "max_seconds": 15, "max_bytes": 95_000_000, "size_label": "95 MB",
             "mime_types": ["video/mp4", "video/quicktime", "video/x-flv", "video/x-msvideo", "video/3gpp", "video/mpeg"]},
}
VIDEO_MARKETPLACES = frozenset(VIDEO_RULES)


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
    problem = video_problem(row, marketplace)
    if problem:
        raise ValueError(problem)
    return {**video_view(row), "checksum": row.checksum, "stored_filename": row.stored_filename}


def video_problem(row: ListingVideo, marketplace: str) -> str | None:
    """Why this marketplace would refuse the video, in the seller's words. None when it fits."""
    rules = VIDEO_RULES.get(marketplace)
    if rules is None:
        return "This marketplace does not take listing videos from Anteroom."
    name = rules["name"]
    if "shorter_than" in rules and row.duration_seconds >= rules["shorter_than"]:
        return f"{name} needs a video shorter than {rules['shorter_than']} seconds."
    if not rules.get("min_seconds", 0) <= row.duration_seconds <= rules.get("max_seconds", math.inf):
        return f"{name} needs a video from {rules['min_seconds']} to {rules['max_seconds']} seconds long."
    if row.size_bytes > rules.get("max_bytes", math.inf):
        return f"{name} accepts videos up to {rules['size_label']}."
    if "mime_types" in rules and row.mime_type not in rules["mime_types"]:
        return f"{name} does not accept this video format. Use an MP4 or MOV file."
    return None


def video_bytes(video: dict) -> bytes:
    """The selected file, refused when it is no longer the one the job selected."""
    path = VIDEOS_DIR / Path(str(video.get("stored_filename") or "")).name
    if not path.name or not path.is_file():
        raise ValueError("The selected listing video is missing. Choose another video or clear it.")
    data = path.read_bytes()
    if len(data) != video.get("size_bytes") or hashlib.sha256(data).hexdigest() != video.get("checksum"):
        raise ValueError("The listing video changed. Choose it again before listing.")
    return data
