"""Images the seller attaches to a chat message.

The composer sends each image as a data URL with the message. The file is
stored under the app data folder, never in SQLite; the message keeps a short
record of each one so the chat can show it again. The model sees the images as
``image_url`` parts on that turn only.
"""

from __future__ import annotations

import base64
import binascii
import re
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from vendoo_studio.config import user_data_root
from vendoo_studio.services.photos import (
    HEIC_MIME,
    THUMBNAIL_SIZES,
    _heic_to_jpeg,
    _normalize_mime,
    save_jpeg_thumbnail,
)

MAX_ATTACHMENTS = 6
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
STORED_EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
STORED_MIME = {extension: mime for mime, extension in STORED_EXTENSIONS.items()}
# The stored file name is the attachment's id: 32 hex digits and the extension.
ATTACHMENT_ID = re.compile(r"^[0-9a-f]{32}\.(?:jpg|png|webp)$")
DATA_URL = re.compile(r"^data:([\w.+-]+/[\w.+-]+)?(?:;[\w=.+-]+)*;base64,(.*)$", re.DOTALL)


def attachments_dir() -> Path:
    path = user_data_root() / "chat-attachments"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _decode(data_url: str) -> tuple[str | None, bytes]:
    match = DATA_URL.match(data_url.strip())
    if not match:
        raise ValueError("Attach images as image files.")
    try:
        contents = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("That image could not be read.") from exc
    return match.group(1), contents


def save_attachment(name: str, data_url: str, *, collection_id: str | None = None) -> dict[str, Any]:
    """Store one attached image and return the record its message keeps."""
    content_type, contents = _decode(data_url)
    if not contents:
        raise ValueError("That image is empty.")
    if len(contents) > MAX_ATTACHMENT_BYTES:
        raise ValueError(f"{name or 'That image'} is larger than {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB.")
    from vendoo_studio.services.service_context import current
    context = current()
    if context is not None:
        if not collection_id:
            raise ValueError('Hosted attachments require a conversation')
        from vendoo_studio.services.hosted_photos import normalize
        contents, width, height = normalize(contents)
        attachment_id = context.photos.new_key(collection_id, '.jpg')
        try:
            context.photos.put(attachment_id, contents)
        except Exception:
            # A response can fail after Storage accepted the object.
            try:
                context.photos.delete(attachment_id)
            except Exception:
                pass
            raise
        return {'id': attachment_id, 'name': (name or 'image').strip()[:200] or 'image',
                'mime_type': 'image/jpeg', 'size_bytes': len(contents), 'width': width, 'height': height}
    try:
        mime = _normalize_mime(content_type, contents)
    except ValueError as exc:
        raise ValueError(f"{name or 'That file'} is not a JPEG, PNG, WebP or HEIC image.") from exc
    if mime in HEIC_MIME or Path(name or "").suffix.lower() in (".heic", ".heif"):
        contents = _heic_to_jpeg(contents)
        mime = "image/jpeg"
    try:
        with Image.open(BytesIO(contents)) as image:
            image.load()
            width, height = image.size
    except Exception as exc:
        raise ValueError(f"{name or 'That file'} is not a valid image.") from exc
    attachment_id = f"{uuid.uuid4().hex}{STORED_EXTENSIONS[mime]}"
    (attachments_dir() / attachment_id).write_bytes(contents)
    return {
        "id": attachment_id,
        "name": (name or "image").strip()[:200] or "image",
        "mime_type": mime,
        "size_bytes": len(contents),
        "width": width,
        "height": height,
    }


def save_attachments(items: list[tuple[str, str]], *, collection_id: str | None = None) -> list[dict[str, Any]]:
    """Store every image of one message, or none of them when one is refused."""
    if len(items) > MAX_ATTACHMENTS:
        raise ValueError(f"Attach at most {MAX_ATTACHMENTS} images to one message.")
    saved: list[dict[str, Any]] = []
    try:
        for name, data_url in items:
            saved.append(save_attachment(name, data_url, collection_id=collection_id))
    except Exception:
        delete_attachments(saved)
        raise
    return saved


def attachment_thumbnail(path: Path, size: int) -> Path:
    """A JPEG at most ``size`` px on a side, cached beside the original."""
    if size not in THUMBNAIL_SIZES:
        raise ValueError(f"Unsupported thumbnail size: {size}")
    thumb = path.with_name(f"{path.stem}_{size}.jpg")
    if thumb.is_file() and thumb.stat().st_size > 0 and thumb.stat().st_mtime >= path.stat().st_mtime:
        return thumb
    save_jpeg_thumbnail(path.read_bytes(), thumb, size)
    return thumb


def delete_attachments(attachments: list[dict[str, Any]] | None) -> None:
    from vendoo_studio.services.service_context import current
    context = current()
    for attachment in attachments or []:
        if context is not None:
            context.photos.delete(str(attachment.get('id') or ''))
            continue
        path = attachment_path(str(attachment.get("id") or ""))
        if path is None:
            continue
        path.unlink(missing_ok=True)
        for size in THUMBNAIL_SIZES:
            path.with_name(f"{path.stem}_{size}.jpg").unlink(missing_ok=True)


def attachment_path(attachment_id: str) -> Path | None:
    """The stored file for an id, or None for anything that is not one."""
    from vendoo_studio.services.service_context import current
    context = current()
    if context is not None:
        return context.photos.local_path(attachment_id)
    if not ATTACHMENT_ID.match(attachment_id):
        return None
    return attachments_dir() / attachment_id


def image_parts(attachments: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """The model's view of a message's images: one data-URL ``image_url`` part each."""
    parts: list[dict[str, Any]] = []
    for attachment in attachments or []:
        attachment_id = str(attachment.get("id") or "")
        path = attachment_path(attachment_id)
        if path is None or not path.is_file():
            continue
        mime = STORED_MIME[path.suffix]
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
    return parts


def with_images(content: str, attachments: list[dict[str, Any]] | None) -> str | list[dict[str, Any]]:
    """A user message's content with its images, as the providers' chat takes it."""
    parts = image_parts(attachments)
    if not parts:
        return content
    return [{"type": "text", "text": content or "See the attached images."}, *parts]


def attach_to_last_user_turn(messages: list[dict], attachments: list[dict[str, Any]] | None) -> list[dict]:
    """Give the newest user message its images. Earlier turns stay text only."""
    if not attachments:
        return messages
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            messages[index] = {**message, "content": with_images(message["content"], attachments)}
            break
    return messages
