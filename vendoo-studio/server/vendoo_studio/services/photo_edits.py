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
import math
import sys
import threading
import uuid
from io import BytesIO
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field, model_validator

from vendoo_studio.services import photos

SAVE_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
PREVIEW_MAX_SIDE = 1600
# Saves render from the source and swap files; one at a time keeps two quick
# saves of the same photo from each deleting the other's file.
_write_lock = threading.Lock()


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
    straighten: float = Field(0, ge=-45, le=45)
    """Degrees clockwise; the frame zooms in so no empty corners show."""
    crop: CropBox | None = None
    exposure: float = Field(0, ge=-2, le=2)
    """Stops: +1 doubles the light."""
    brightness: float = Field(0, ge=-100, le=100)
    contrast: float = Field(0, ge=-100, le=100)
    background: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
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
    return Path(photos.PHOTOS_DIR) / "masks" / f"{Path(source_filename).stem}.png"


def _vision_mask(img: Image.Image) -> Image.Image:
    """The item's soft cut-out mask from Apple Vision, run on this Mac.

    ``VNGenerateForegroundInstanceMaskRequest`` is the subject lifting in
    Photos and Preview (macOS 14+). It needs no model download and runs on the
    Neural Engine.
    """
    if sys.platform != "darwin":
        raise BackgroundRemovalError("Background removal runs on a Mac.")
    try:
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
        raw = Quartz.CVPixelBufferGetBaseAddress(buffer_ref).as_buffer(row * height)
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
    source = Path(photos.PHOTOS_DIR) / source_filename
    if not source.exists():
        raise FileNotFoundError(source_filename)
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
    source = Path(photos.PHOTOS_DIR) / source_filename
    if not source.exists():
        raise FileNotFoundError(source_filename)
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
    stored_name = f"{uuid.uuid4().hex}{source.suffix}"
    (Path(photos.PHOTOS_DIR) / stored_name).write_bytes(contents)
    return {
        "stored_filename": stored_name,
        "size_bytes": len(contents),
        "checksum": hashlib.sha256(contents).hexdigest(),
        "width": img.width,
        "height": img.height,
    }


def source_meta(source_filename: str) -> dict:
    """Record fields for pointing a photo back at its untouched upload."""
    path = Path(photos.PHOTOS_DIR) / source_filename
    contents = path.read_bytes()
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


def save_edits(repo, photo, edits: PhotoEdits):
    """Render ``edits`` from the photo's upload and serve the result; the upload is kept."""
    if edits.is_original():
        return reset_edits(repo, photo)
    with _write_lock:
        source = photo.source_filename or photo.stored_filename
        meta = write_edit(source, photo.mime_type, edits)
        previous = photo.stored_filename if photo.source_filename else None
        try:
            photo = repo.replace_photo_file(photo, meta, source_filename=source, edits=edits.model_dump())
        except Exception:
            (Path(photos.PHOTOS_DIR) / meta["stored_filename"]).unlink(missing_ok=True)
            raise
        if previous:
            (Path(photos.PHOTOS_DIR) / previous).unlink(missing_ok=True)
            photos.delete_thumbnails(previous)
    return photo


def reset_edits(repo, photo):
    """Serve the untouched upload again."""
    with _write_lock:
        if not photo.source_filename:
            return photo
        previous = photo.stored_filename
        photo = repo.replace_photo_file(photo, source_meta(photo.source_filename), source_filename=None, edits=None)
        (Path(photos.PHOTOS_DIR) / previous).unlink(missing_ok=True)
        photos.delete_thumbnails(previous)
    return photo
