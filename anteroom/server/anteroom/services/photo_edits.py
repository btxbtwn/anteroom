"""Non-destructive photo edits: a recipe rendered from the untouched upload.

Like Photos, Lightroom and Photoroom, an edit is a list of settings rather
than a change to the file. The upload stays on disk as the photo's source;
each save renders the recipe from it into a new file that every send, preview
and model reads, and Reset points the photo back at the upload.

The order follows those editors: tone first, then the background is swapped
behind the cut-out item, then the frame is turned, straightened and cropped.
"""
from __future__ import annotations

import hashlib
import logging
import math
import sys
import threading
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field, model_validator

from anteroom.models.conversation import Photo
from anteroom.services import photos
from anteroom.services.photo_store import photo_store

log = logging.getLogger(__name__)

SAVE_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
PREVIEW_MAX_SIDE = 1600


class _PhotoLockEntry:
    __slots__ = ("lock", "waiters")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.waiters = 0


# Serialize concurrent edits to the same photo without blocking edits to other
# photos or holding a Postgres transaction open across Storage/Pillow I/O.
_photo_locks_guard = threading.Lock()
_photo_locks: dict[tuple[str, str, str], _PhotoLockEntry] = {}


@contextmanager
def _photo_lock(conv_id: str, photo_id: str):
    from anteroom.services.service_context import current

    context = current()
    key = (context.seller_id if context is not None else "", conv_id, photo_id)
    with _photo_locks_guard:
        entry = _photo_locks.get(key)
        if entry is None:
            entry = _PhotoLockEntry()
            _photo_locks[key] = entry
        entry.waiters += 1
    try:
        with entry.lock:
            yield
    finally:
        with _photo_locks_guard:
            entry.waiters -= 1
            if entry.waiters == 0 and _photo_locks.get(key) is entry:
                del _photo_locks[key]


class PhotoNotFoundError(LookupError):
    pass


class PhotoConflictError(RuntimeError):
    pass


class CropBox(BaseModel):
    """A crop as fractions of the turned and straightened photo."""

    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def _inside(self) -> CropBox:
        if self.x + self.width > 1.0001 or self.y + self.height > 1.0001:
            raise ValueError("The crop must stay inside the photo.")
        return self


class PhotoEdits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rotate: Literal[0, 90, 180, 270] = 0
    """Quarter turns, clockwise."""
    straighten: float = Field(default=0, ge=-45, le=45)
    """Degrees clockwise; the frame zooms in so no empty corners show."""
    crop: CropBox | None = None
    exposure: float = Field(default=0, ge=-2, le=2)
    """Stops: +1 doubles the light."""
    brightness: float = Field(default=0, ge=-100, le=100)
    contrast: float = Field(default=0, ge=-100, le=100)
    background: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    """Colour behind the cut-out item; None keeps the photo's own background."""

    def is_original(self) -> bool:
        full = self.crop is None or (self.crop.width >= 0.9999 and self.crop.height >= 0.9999)
        return full and self == PhotoEdits(crop=self.crop)

    def turned(self, degrees: int) -> PhotoEdits:
        """These edits with the frame turned a further ``degrees`` clockwise, crop included."""
        turns = (degrees // 90) % 4
        crop = self.crop
        for _ in range(turns):
            if crop:
                crop = CropBox(x=max(0.0, 1 - crop.y - crop.height), y=crop.x, width=crop.height, height=crop.width)
        return self.model_copy(update={"rotate": (self.rotate + degrees) % 360, "crop": crop})


class BackgroundRemovalError(ValueError):
    pass


def _source_image(path: Path) -> Image.Image:
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            flat = Image.new("RGB", img.size, "white")
            flat.paste(img, mask=img.getchannel("A"))
            return flat
        return img.convert("RGB")


def _adjust(img: Image.Image, edits: PhotoEdits) -> Image.Image:
    if not (edits.exposure or edits.brightness or edits.contrast):
        return img
    pixels = np.asarray(img, dtype=np.float32)
    if edits.exposure:
        pixels = pixels * (2.0 ** edits.exposure)
    if edits.brightness:
        pixels = pixels + edits.brightness * 0.75
    if edits.contrast:
        mean = float(np.clip(pixels, 0, 255).mean())
        pixels = (pixels - mean) * (1 + edits.contrast / 100) + mean
    return Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8), "RGB")


def _straighten(img: Image.Image, degrees: float, fill: tuple[int, int, int]) -> Image.Image:
    """Rotate by a small angle and keep the largest same-shaped frame with no empty corners."""
    if not degrees:
        return img
    width, height = img.size
    angle = math.radians(abs(degrees))
    cos, sin = math.cos(angle), math.sin(angle)
    scale = min(width / (width * cos + height * sin), height / (width * sin + height * cos))
    rotated = img.rotate(-degrees, resample=Image.Resampling.BICUBIC, fillcolor=fill)
    keep_w, keep_h = max(1, int(width * scale)), max(1, int(height * scale))
    left, top = (width - keep_w) // 2, (height - keep_h) // 2
    return rotated.crop((left, top, left + keep_w, top + keep_h))


def _crop(img: Image.Image, crop: CropBox | None) -> Image.Image:
    if crop is None:
        return img
    width, height = img.size
    left, top = round(crop.x * width), round(crop.y * height)
    right = min(width, max(left + 1, round((crop.x + crop.width) * width)))
    bottom = min(height, max(top + 1, round((crop.y + crop.height) * height)))
    return img.crop((left, top, right, bottom))


def _hex(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def mask_path(source_filename: str) -> Path:
    return photo_store().cache_dir("masks") / f"{Path(source_filename).stem}.png"


def _vision_mask(img: Image.Image) -> Image.Image:
    """The item's soft cut-out mask from Apple Vision, run on this Mac.

    ``VNGenerateForegroundInstanceMaskRequest`` is the subject lifting in
    Photos and Preview (macOS 14+). It needs no model download and runs on the
    Neural Engine.
    """
    if sys.platform != "darwin":
        raise BackgroundRemovalError("Background removal runs on a Mac.")
    try:
        import objc
        import Quartz
        import Vision
        from Foundation import NSData
    except ImportError as exc:
        raise BackgroundRemovalError("Background removal is not available in this build.") from exc
    if not hasattr(Vision, "VNGenerateForegroundInstanceMaskRequest"):
        raise BackgroundRemovalError("Background removal needs macOS 14 or later.")

    buffer = BytesIO()
    img.save(buffer, "PNG")
    data = buffer.getvalue()
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(NSData.dataWithBytes_length_(data, len(data)), None)
    request = Vision.VNGenerateForegroundInstanceMaskRequest.alloc().init()
    if handler is None or request is None:
        raise BackgroundRemovalError("Background removal could not initialize Apple Vision.")
    ok, error = handler.performRequests_error_([request], None)
    if not ok:
        raise BackgroundRemovalError(f"Background removal failed: {error}")
    results = request.results() or []
    if not results:
        raise BackgroundRemovalError("Anteroom could not find an item to cut out in this photo.")
    observation = results[0]
    buffer_ref, error = observation.generateScaledMaskForImageForInstances_fromRequestHandler_error_(
        observation.allInstances(), handler, None)
    if buffer_ref is None:
        raise BackgroundRemovalError(f"Background removal failed: {error}")
    Quartz.CVPixelBufferLockBaseAddress(buffer_ref, Quartz.kCVPixelBufferLock_ReadOnly)
    try:
        width = Quartz.CVPixelBufferGetWidth(buffer_ref)
        height = Quartz.CVPixelBufferGetHeight(buffer_ref)
        row = Quartz.CVPixelBufferGetBytesPerRow(buffer_ref)
        address = Quartz.CVPixelBufferGetBaseAddress(buffer_ref)
        if not isinstance(address, objc.varlist):
            raise BackgroundRemovalError("Background removal returned an empty pixel buffer.")
        raw = address.as_buffer(row * height)
        # One 32-bit float per pixel, 0 = background, 1 = item.
        values = np.frombuffer(raw, dtype=np.float32).reshape(height, row // 4)[:, :width]
        mask = np.clip(values * 255 + 0.5, 0, 255).astype(np.uint8)
    finally:
        Quartz.CVPixelBufferUnlockBaseAddress(buffer_ref, Quartz.kCVPixelBufferLock_ReadOnly)
    result = Image.fromarray(mask, "L")
    return result if result.size == img.size else result.resize(img.size, Image.Resampling.BILINEAR)


def foreground_mask(source_filename: str, img: Image.Image) -> Image.Image:
    """The cached cut-out mask of the upright source; finding it is the slow step."""
    path = mask_path(source_filename)
    if path.exists():
        with Image.open(path) as cached:
            if cached.size == img.size:
                return cached.convert("L")
    mask = _vision_mask(img)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    mask.save(tmp, "PNG")
    tmp.replace(path)
    return mask


def render(source_filename: str, edits: PhotoEdits, *, max_side: int | None = None, crop: bool = True) -> Image.Image:
    try:
        source = photo_store().local_path(source_filename)
    except (FileNotFoundError, ValueError):
        raise FileNotFoundError(source_filename) from None
    img = _source_image(source)
    mask = foreground_mask(source_filename, img) if edits.background else None
    if max_side and max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        if mask is not None:
            mask = mask.resize(img.size, Image.Resampling.BILINEAR)
    img = _adjust(img, edits)
    fill = (255, 255, 255)
    if edits.background and mask is not None:
        fill = _hex(edits.background)
        img = Image.composite(img, Image.new("RGB", img.size, fill), mask)
    if edits.rotate:
        img = img.rotate(-edits.rotate, expand=True)
    img = _straighten(img, edits.straighten, fill)
    return _crop(img, edits.crop) if crop else img


def preview_jpeg(source_filename: str, edits: PhotoEdits, *, crop: bool) -> bytes:
    img = render(source_filename, edits, max_side=PREVIEW_MAX_SIDE, crop=crop)
    buffer = BytesIO()
    img.save(buffer, "JPEG", quality=85)
    return buffer.getvalue()


def write_edit(source_filename: str, mime_type: str, edits: PhotoEdits) -> dict:
    """Render the edits from the source into a new file and return its record fields."""
    fmt = SAVE_FORMATS.get(mime_type)
    if not fmt:
        raise ValueError("This photo format cannot be edited")
    try:
        source = photo_store().local_path(source_filename)
    except (FileNotFoundError, ValueError):
        raise FileNotFoundError(source_filename) from None
    with Image.open(source) as original:
        icc_profile = original.info.get("icc_profile")
    img = render(source_filename, edits)
    options: dict = {"icc_profile": icc_profile} if icc_profile else {}
    if fmt == "JPEG":
        options.update(quality=95, subsampling=0)
    elif fmt == "WEBP":
        options.update(quality=95)
    buffer = BytesIO()
    img.save(buffer, fmt, **options)
    contents = buffer.getvalue()
    stored_name = photo_store().edited_key(source_filename)
    photo_store().put(stored_name, contents)
    return {
        "stored_filename": stored_name,
        "size_bytes": len(contents),
        "checksum": hashlib.sha256(contents).hexdigest(),
        "width": img.width,
        "height": img.height,
    }


def source_meta(source_filename: str) -> dict:
    """Record fields for pointing a photo back at its untouched upload."""
    contents = photo_store().read(source_filename)
    with Image.open(BytesIO(contents)) as img:
        width, height = img.size
    return {
        "stored_filename": source_filename,
        "size_bytes": len(contents),
        "checksum": hashlib.sha256(contents).hexdigest(),
        "width": width,
        "height": height,
    }


def current_edits(photo) -> PhotoEdits:
    return PhotoEdits.model_validate(photo.edits) if photo.edits else PhotoEdits()


def _cleanup_uploaded(stored_filename: str) -> None:
    try:
        photo_store().delete(stored_filename)
    except Exception:
        log.warning("Failed to clean up uncommitted edited photo %s", stored_filename, exc_info=True)


def _cleanup_previous(previous: str) -> None:
    try:
        photo_store().delete(previous)
    except Exception:
        log.warning("Failed to delete superseded edited photo %s after commit", previous, exc_info=True)
    try:
        photos.delete_thumbnails(previous)
    except Exception:
        log.warning("Failed to delete thumbnails for superseded edited photo %s after commit", previous, exc_info=True)


def read_preview_source(db, conv_id: str, photo_id: str) -> str:
    """Read the source filename for previewing, then release the DB transaction before rendering."""
    from anteroom.services import background_tasks
    from anteroom.services.service_context import current

    try:
        if current() is not None:
            background_tasks.check_idle(db, conv_id)
        photo = db.query(Photo).filter(Photo.id == photo_id, Photo.conversation_id == conv_id).first()
        if photo is None:
            raise PhotoNotFoundError("Photo not found")
        return photo.source_filename or photo.stored_filename
    finally:
        db.rollback()


def apply_photo_edit(
    repo,
    conv_id: str,
    photo_id: str,
    resolve_edits: Callable[[PhotoEdits], PhotoEdits | None],
):
    """Apply or reset photo edits with per-photo locking and no DB transaction open during Storage/Pillow I/O."""
    from anteroom.services import background_tasks
    from anteroom.services.service_context import current

    db = repo.db
    with _photo_lock(conv_id, photo_id):
        # Phase 1: short unlocked read of current metadata, then release the DB transaction.
        try:
            if current() is not None:
                background_tasks.check_idle(db, conv_id)
            photo = db.query(Photo).filter(Photo.id == photo_id, Photo.conversation_id == conv_id).first()
            if photo is None:
                raise PhotoNotFoundError("Photo not found")
            target = resolve_edits(current_edits(photo))
            current_stored = photo.stored_filename
            current_source = photo.source_filename
            mime_type = photo.mime_type
        finally:
            db.rollback()

        if target is None and not current_source:
            return photo

        # Phase 2: Storage download + Pillow render + Storage upload outside any DB transaction.
        if target is None:
            assert current_source is not None
            meta = source_meta(current_source)
            uploaded_key: str | None = None
            new_source: str | None = None
            new_edits: dict | None = None
            previous: str | None = current_stored
        else:
            source = current_source or current_stored
            meta = write_edit(source, mime_type, target)
            uploaded_key = meta["stored_filename"]
            new_source = source
            new_edits = target.model_dump()
            previous = current_stored if current_source else None

        # Phase 3: short locked transaction to verify state and commit the new metadata.
        committed = False
        try:
            if current() is not None:
                background_tasks.require_idle(db, conv_id)
            fresh = (
                db.query(Photo)
                .filter(Photo.id == photo_id, Photo.conversation_id == conv_id)
                .populate_existing()
                .with_for_update()
                .first()
            )
            if fresh is None:
                raise PhotoNotFoundError("Photo not found")
            if fresh.stored_filename != current_stored or fresh.source_filename != current_source:
                raise PhotoConflictError("Photo changed while editing; try again.")
            photo = repo.replace_photo_file(fresh, meta, source_filename=new_source, edits=new_edits)
            committed = True
            # End the post-commit refresh read transaction before post-commit Storage cleanup.
            db.rollback()
        except BaseException:
            if not committed:
                db.rollback()
                if uploaded_key:
                    _cleanup_uploaded(uploaded_key)
            raise

        # Phase 4: best-effort post-commit cleanup outside any DB transaction.
        if previous:
            _cleanup_previous(previous)

    return photo


def save_edits(repo, photo, edits: PhotoEdits):
    """Render ``edits`` from the photo's upload and serve the result; the upload is kept."""
    target = None if edits.is_original() else edits
    return apply_photo_edit(repo, photo.conversation_id, photo.id, lambda _cur: target)
