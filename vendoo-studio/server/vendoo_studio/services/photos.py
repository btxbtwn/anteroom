from __future__ import annotations

import hashlib
import os
import uuid
from io import BytesIO
from pathlib import Path

from fastapi import UploadFile
from PIL import Image, ImageOps

from vendoo_studio.config import PHOTOS_DIR, ALLOWED_PHOTO_MIME, MAX_PHOTO_SIZE_MB

MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
}
HEIC_MIME = {"image/heic", "image/heif"}
HEIC_JPEG_QUALITY = 92


def _normalize_mime(content_type: str | None, contents: bytes) -> str:
    raw = (content_type or "").split(";")[0].strip().lower()
    if raw == "image/jpg":
        raw = "image/jpeg"
    if raw in ALLOWED_PHOTO_MIME:
        return raw
    if contents.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if contents.startswith(b"\x89PNG"):
        return "image/png"
    if contents.startswith(b"RIFF") and b"WEBP" in contents[:16]:
        return "image/webp"
    raise ValueError(f"Unsupported file type: {content_type}")


def _heic_to_jpeg(contents: bytes) -> bytes:
    """Upright JPEG of a HEIC photo: AI models and marketplaces take JPEG, not HEIC."""
    try:
        with Image.open(BytesIO(contents)) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=HEIC_JPEG_QUALITY)
    except Exception as exc:
        raise ValueError("File is not a valid image") from exc
    return buf.getvalue()


def process_bytes(contents: bytes, filename: str | None = None, content_type: str | None = None) -> dict:
    mime_type = _normalize_mime(content_type, contents)
    size_mb = len(contents) / (1024 * 1024)
    if size_mb > MAX_PHOTO_SIZE_MB:
        raise ValueError(f"File too large ({size_mb:.1f}MB). Max {MAX_PHOTO_SIZE_MB}MB")

    ext = os.path.splitext(filename or "image.jpg")[1].lower()
    if mime_type in HEIC_MIME or ext in (".heic", ".heif"):
        contents = _heic_to_jpeg(contents)
        mime_type = "image/jpeg"
        ext = ".jpg"
    elif ext not in (".jpg", ".jpeg", ".png", ".webp"):
        ext = MIME_EXTENSIONS.get(mime_type, ".jpg")

    stored_name = f"{uuid.uuid4().hex}{ext}"
    filepath = Path(PHOTOS_DIR) / stored_name

    checksum = hashlib.sha256(contents).hexdigest()

    width = None
    height = None
    try:
        img = Image.open(BytesIO(contents))
        img.load()
        width, height = img.size
    except Exception as exc:
        if mime_type in {"image/jpeg", "image/png", "image/webp"}:
            raise ValueError("File is not a valid image") from exc

    # Invalid downloads must not leave an untracked file on disk.
    filepath.write_bytes(contents)

    return {
        "original_filename": filename or stored_name,
        "stored_filename": stored_name,
        "mime_type": mime_type,
        "size_bytes": len(contents),
        "checksum": checksum,
        "width": width,
        "height": height,
    }


async def process_upload(conv_id: str, file: UploadFile):
    contents = await file.read()
    return process_bytes(contents, file.filename, file.content_type)


THUMBNAIL_SIZES = (96, 192)
THUMBNAIL_MIME = "image/jpeg"
COVER_THUMB_SIZE = 96


def photo_url(photo_id: str, checksum: str | None) -> str:
    """The original's URL, versioned by content so a rotated photo is refetched."""
    return f"/api/photos/{photo_id}" + (f"?v={checksum[:12]}" if checksum else "")


def cover_thumb_url(photo_id: str, checksum: str | None) -> str:
    url = f"/api/photos/{photo_id}/thumb?size={COVER_THUMB_SIZE}"
    return url + (f"&v={checksum[:12]}" if checksum else "")


def thumbnail_path(stored_filename: str, size: int) -> Path:
    stem = Path(stored_filename).stem
    return Path(PHOTOS_DIR) / "thumbs" / f"{stem}_{size}.jpg"


def get_or_create_thumbnail(stored_filename: str, size: int) -> Path:
    """Return a cached square-ish JPEG thumbnail, rendering it on first request."""
    source = Path(PHOTOS_DIR) / stored_filename
    if not source.exists():
        raise FileNotFoundError(stored_filename)

    target = thumbnail_path(stored_filename, size)
    if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as img:
        img = ImageOps.exif_transpose(img)
        img.thumbnail((size, size), Image.Resampling.LANCZOS)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        tmp = target.with_suffix(f".{uuid.uuid4().hex}.tmp")
        img.save(tmp, "JPEG", quality=82, optimize=True)
    os.replace(tmp, target)
    return target


def delete_thumbnails(stored_filename: str) -> None:
    for size in THUMBNAIL_SIZES:
        path = thumbnail_path(stored_filename, size)
        if path.exists():
            try:
                os.remove(path)
            except OSError:
                pass


def delete_photo_files(photo) -> None:
    """Remove a photo's served file, its kept upload, and what was derived from them."""
    from vendoo_studio.services.photo_edits import mask_path

    names = [name for name in (photo.stored_filename, photo.source_filename) if name]
    for name in names:
        (Path(PHOTOS_DIR) / name).unlink(missing_ok=True)
        delete_thumbnails(name)
    mask_path(photo.source_filename or photo.stored_filename).unlink(missing_ok=True)
