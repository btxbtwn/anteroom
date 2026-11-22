"""Non-destructive photo edits: a recipe rendered from the untouched upload.

Like Photos, Lightroom and Photoroom, an edit is a list of settings rather
than a change to the file. The upload stays on disk as the photo's source;
each save renders the recipe from it into a new file that every send, preview
and model reads, and Reset points the photo back at the upload.

The order follows those editors: an AI Studio result (Photoroom) replaces the
upload as the base, tone and a filter come first, then the background is swapped
behind the cut-out item, the frame is turned, mirrored, straightened and
cropped, and last come redactions, annotations and a border, drawn on the
finished frame (their positions are fractions of it).
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
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import inspect

from anteroom.database import end_read
from anteroom.models.conversation import Photo
from anteroom.services import image_decode, photos
from anteroom.services.photo_store import photo_store
from anteroom.services.photoroom import StudioOptions

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


HEX_COLOR = r"^#[0-9a-fA-F]{6}$"
FILTERS = ("vivid", "crisp", "warm", "cool", "bw", "studio")
MAX_MARKS = 30


def _turn_box(box: CropBox) -> dict:
    """A box's fields after the frame turns a quarter clockwise."""
    return {"x": max(0.0, 1 - box.y - box.height), "y": box.x, "width": box.height, "height": box.width}


class Redaction(CropBox):
    """A region hidden on the finished frame, such as a name, address or barcode."""

    style: Literal["pixelate", "blur"] = "pixelate"


class Annotation(BaseModel):
    """An arrow, box, ellipse or text label drawn on the finished frame, from (x1, y1) to (x2, y2)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["arrow", "rectangle", "ellipse", "text"]
    x1: float = Field(ge=0, le=1)
    y1: float = Field(ge=0, le=1)
    x2: float = Field(ge=0, le=1)
    y2: float = Field(ge=0, le=1)
    color: str = Field(default="#E5484D", pattern=HEX_COLOR)
    text: str | None = Field(default=None, max_length=60)

    @model_validator(mode="after")
    def _text(self) -> Annotation:
        if self.kind == "text":
            if not (self.text or "").strip():
                raise ValueError("A text label needs some text.")
            self.text = self.text.strip() if self.text else self.text
        elif self.text is not None:
            raise ValueError("Only text labels carry text.")
        return self

    def turned(self) -> Annotation:
        return self.model_copy(update={"x1": 1 - self.y1, "y1": self.x1, "x2": 1 - self.y2, "y2": self.x2})


class Border(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: float = Field(gt=0, le=0.25)
    """A fraction of the finished photo's shorter side."""
    color: str = Field(default="#FFFFFF", pattern=HEX_COLOR)


class StudioEdit(BaseModel):
    """Photoroom's result for this photo, kept beside the upload; edits render from it."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=300)
    options: StudioOptions


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
    background: str | None = Field(default=None, pattern=HEX_COLOR)
    """Colour behind the cut-out item (Apple Vision on the Mac); None keeps the photo's own background."""
    flip_horizontal: bool = False
    flip_vertical: bool = False
    filter: Literal["vivid", "crisp", "warm", "cool", "bw", "studio"] | None = None
    redactions: list[Redaction] = Field(default_factory=list, max_length=MAX_MARKS)
    annotations: list[Annotation] = Field(default_factory=list, max_length=MAX_MARKS)
    border: Border | None = None
    studio: StudioEdit | None = None
    """AI Studio's result, the base every other edit renders from."""

    @model_validator(mode="after")
    def _one_background(self) -> PhotoEdits:
        if self.studio is not None and self.background is not None:
            raise ValueError("AI Studio already set this photo's background. Reset it to use another background.")
        return self

    def is_original(self) -> bool:
        full = self.crop is None or (self.crop.width >= 0.9999 and self.crop.height >= 0.9999)
        return full and self == PhotoEdits(crop=self.crop)

    def turned(self, degrees: int) -> PhotoEdits:
        """These edits with the frame turned a further ``degrees`` clockwise, crop and marks included."""
        turns = (degrees // 90) % 4
        crop, redactions, annotations = self.crop, list(self.redactions), list(self.annotations)
        for _ in range(turns):
            if crop:
                crop = CropBox(**_turn_box(crop))
            redactions = [Redaction(**_turn_box(box), style=box.style) for box in redactions]
            annotations = [mark.turned() for mark in annotations]
        # Rotation renders before the flip, so turning a mirrored frame a quarter swaps the mirror's axis.
        horizontal, vertical = ((self.flip_vertical, self.flip_horizontal) if turns % 2
                                else (self.flip_horizontal, self.flip_vertical))
        return self.model_copy(update={"rotate": (self.rotate + degrees) % 360, "crop": crop,
                                       "redactions": redactions, "annotations": annotations,
                                       "flip_horizontal": horizontal, "flip_vertical": vertical})


class BackgroundRemovalError(ValueError):
    pass


def _source_image(path: Path, *, max_side: int | None = None) -> Image.Image:
    img, _size = image_decode.decode(path.read_bytes(), max_side=max_side)
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


def _filter(img: Image.Image, name: str | None) -> Image.Image:
    """A one-tap look, like the presets in Photos and Photoroom."""
    if name is None:
        return img
    if name == "vivid":
        return ImageEnhance.Contrast(ImageEnhance.Color(img).enhance(1.35)).enhance(1.08)
    if name == "crisp":
        return ImageEnhance.Contrast(ImageEnhance.Sharpness(img).enhance(1.9)).enhance(1.06)
    if name == "bw":
        return ImageOps.grayscale(img).convert("RGB")
    pixels = np.asarray(img, dtype=np.float32)
    if name == "warm":
        pixels = pixels * np.array([1.08, 1.0, 0.9], dtype=np.float32)
    elif name == "cool":
        pixels = pixels * np.array([0.92, 1.0, 1.08], dtype=np.float32)
    elif name == "studio":
        # Studio clean: lift the light end so a pale backdrop reads as white, keep the item's tone.
        pixels = pixels * 1.04 * (255 / 238)
    return Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8), "RGB")


def _flip(img: Image.Image, edits: PhotoEdits) -> Image.Image:
    if edits.flip_horizontal:
        img = ImageOps.mirror(img)
    if edits.flip_vertical:
        img = ImageOps.flip(img)
    return img


def _pixel_box(img: Image.Image, box: CropBox) -> tuple[int, int, int, int]:
    width, height = img.size
    # Even a thin region at the far edge must include a pixel.
    left, top = min(width - 1, round(box.x * width)), min(height - 1, round(box.y * height))
    right = min(width, max(left + 1, round((box.x + box.width) * width)))
    bottom = min(height, max(top + 1, round((box.y + box.height) * height)))
    return left, top, right, bottom


def _redact(img: Image.Image, redactions: list[Redaction]) -> Image.Image:
    if not redactions:
        return img
    img = img.copy()
    short = min(img.size)
    for box in redactions:
        area = _pixel_box(img, box)
        region = img.crop(area)
        if box.style == "blur":
            # Strong enough that text cannot be read back.
            region = region.filter(ImageFilter.GaussianBlur(max(6.0, short * 0.03)))
        else:
            cell = max(6, short // 40)
            small = (max(1, region.width // cell), max(1, region.height // cell))
            region = region.resize(small, Image.Resampling.BILINEAR).resize(region.size, Image.Resampling.NEAREST)
        img.paste(region, area[:2])
    return img


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow without FreeType sizes the bitmap font only.
        return ImageFont.load_default()


def _annotate(img: Image.Image, annotations: list[Annotation]) -> Image.Image:
    if not annotations:
        return img
    img = img.copy()
    draw = ImageDraw.Draw(img)
    width, height = img.size
    stroke = max(2, round(min(width, height) * 0.008))
    for mark in annotations:
        color = _hex(mark.color)
        x1, y1, x2, y2 = mark.x1 * width, mark.y1 * height, mark.x2 * width, mark.y2 * height
        if mark.kind == "arrow":
            draw.line((x1, y1, x2, y2), fill=color, width=stroke)
            angle = math.atan2(y2 - y1, x2 - x1)
            head = stroke * 5
            points = [(x2, y2)] + [(x2 - head * math.cos(angle + turn), y2 - head * math.sin(angle + turn))
                                   for turn in (0.45, -0.45)]
            draw.polygon(points, fill=color)
        elif mark.kind in ("rectangle", "ellipse"):
            box = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
            if mark.kind == "rectangle":
                draw.rectangle(box, outline=color, width=stroke)
            else:
                draw.ellipse(box, outline=color, width=stroke)
        else:
            size = max(12, round(min(width, height) * 0.05))
            # Light text gets a dark outline and dark text a light one, so it reads on any photo.
            outline = (0, 0, 0) if sum(color) > 382 else (255, 255, 255)
            draw.text((x1, y1), mark.text or "", fill=color, font=_font(size),
                      stroke_width=max(1, size // 12), stroke_fill=outline)
    return img


def _border(img: Image.Image, border: Border | None) -> Image.Image:
    if border is None:
        return img
    pad = max(1, round(min(img.size) * border.width))
    return ImageOps.expand(img, border=pad, fill=_hex(border.color))


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


STUDIO_MISSING = "This photo's AI Studio result is missing. Reset the photo or run AI Studio again."


def render(source_filename: str, edits: PhotoEdits, *, max_side: int | None = None, crop: bool = True) -> Image.Image:
    """The edited photo. ``crop=False`` leaves the frame whole and skips marks and the border, to draw a crop on."""
    if edits.studio is not None:
        try:
            source = photo_store().local_path(edits.studio.key)
        except (FileNotFoundError, ValueError):
            raise ValueError(STUDIO_MISSING) from None
    else:
        try:
            source = photo_store().local_path(source_filename)
        except (FileNotFoundError, ValueError):
            raise FileNotFoundError(source_filename) from None
    img = _source_image(source, max_side=None if edits.background else max_side)
    mask = foreground_mask(source_filename, img) if edits.background else None
    if max_side and max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        if mask is not None:
            mask = mask.resize(img.size, Image.Resampling.BILINEAR)
    img = _filter(_adjust(img, edits), edits.filter)
    fill = (255, 255, 255)
    if edits.background and mask is not None:
        fill = _hex(edits.background)
        img = Image.composite(img, Image.new("RGB", img.size, fill), mask)
    elif edits.studio is not None and edits.studio.options.virtual_model is None:
        fill = _hex(edits.studio.options.background)
    if edits.rotate:
        img = img.rotate(-edits.rotate, expand=True)
    img = _flip(img, edits)
    img = _straighten(img, edits.straighten, fill)
    if not crop:
        return img
    img = _crop(img, edits.crop)
    img = _annotate(_redact(img, edits.redactions), edits.annotations)
    return _border(img, edits.border)


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


def studio_key(edits: dict | None) -> str | None:
    """The kept AI Studio result named by a photo's stored edits, if any."""
    studio = (edits or {}).get("studio") if isinstance(edits, dict) else None
    key = studio.get("key") if isinstance(studio, dict) else None
    return key if isinstance(key, str) and key else None


def keep_studio(current: PhotoEdits, requested: PhotoEdits) -> PhotoEdits:
    """``requested`` with the photo's own AI Studio result: edits may keep or drop it, never name another file."""
    if requested.studio is None or (current.studio is not None and requested.studio.key == current.studio.key):
        return requested.model_copy(update={"studio": current.studio if requested.studio else None})
    raise ValueError("Use AI Studio to change this photo's AI edit.")


def cleanup_uploaded(stored_filename: str) -> None:
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


def read_preview_source(db, conv_id: str, photo_id: str) -> tuple[str, PhotoEdits]:
    """Read the source filename and saved edits for previewing, then release the DB transaction before rendering."""
    from anteroom.services import background_tasks
    from anteroom.services.service_context import current

    try:
        if current() is not None:
            background_tasks.check_idle(db, conv_id)
        photo = db.query(Photo).filter(Photo.id == photo_id, Photo.conversation_id == conv_id).first()
        if photo is None:
            raise PhotoNotFoundError("Photo not found")
        return photo.source_filename or photo.stored_filename, current_edits(photo)
    finally:
        db.rollback()


def _snapshot_photo(photo: Photo) -> Photo:
    """Return a detached Photo copy with loaded column values so post-rollback reads never query."""
    return Photo(**{attr.key: getattr(photo, attr.key) for attr in inspect(Photo).column_attrs})


def apply_photo_edit(
    repo,
    conv_id: str,
    photo_id: str,
    resolve_edits: Callable[[PhotoEdits], PhotoEdits | None],
    *,
    expected_stored: str | None = None,
):
    """Apply or reset photo edits with per-photo locking and no DB transaction open during Storage/Pillow I/O.

    ``expected_stored`` refuses the edit when the photo's served file is no longer that one.
    """
    from anteroom.services import background_tasks
    from anteroom.services.service_context import current

    db = repo.db
    # Hosted routes may enter with an open read transaction (e.g. get_seller_db settings load);
    # release it before waiting on the per-photo lock so contention never holds a pooled connection.
    end_read(db)
    with _photo_lock(conv_id, photo_id):
        # Phase 1: short unlocked read of current metadata, then release the DB transaction.
        try:
            if current() is not None:
                background_tasks.check_idle(db, conv_id)
            photo = db.query(Photo).filter(Photo.id == photo_id, Photo.conversation_id == conv_id).first()
            if photo is None:
                raise PhotoNotFoundError("Photo not found")
            if expected_stored is not None and photo.stored_filename != expected_stored:
                raise PhotoConflictError("Photo changed while editing; try again.")
            target = resolve_edits(current_edits(photo))
            previous_studio = studio_key(photo.edits)
            current_stored = photo.stored_filename
            current_source = photo.source_filename
            mime_type = photo.mime_type
            noop_snapshot = _snapshot_photo(photo) if target is None and not current_source else None
        finally:
            end_read(db)

        if noop_snapshot is not None:
            return noop_snapshot

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
            db.refresh(photo)
            # Keep the saved response snapshot while releasing the refresh transaction.
            photo = _snapshot_photo(photo)
            end_read(db)
        except BaseException:
            db.rollback()
            if not committed and uploaded_key:
                cleanup_uploaded(uploaded_key)
            raise

        # Phase 4: best-effort post-commit cleanup outside any DB transaction.
        if previous:
            _cleanup_previous(previous)
        if previous_studio and previous_studio != (target.studio.key if target and target.studio else None):
            _cleanup_previous(previous_studio)

    return photo


def save_edits(repo, photo, edits: PhotoEdits):
    """Render ``edits`` from the photo's upload and serve the result; the upload is kept."""
    target = None if edits.is_original() else edits
    return apply_photo_edit(repo, photo.conversation_id, photo.id, lambda _cur: target)
