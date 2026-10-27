"""Serve images attached to chat messages, so the chat can show them again."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from vendoo_studio.services.chat_attachments import (
    MAX_ATTACHMENTS,
    STORED_MIME,
    attachment_path,
    attachment_thumbnail,
    save_attachments,
)
from vendoo_studio.services.photos import THUMBNAIL_SIZES

router = APIRouter(tags=["attachments"])


class AttachmentUpload(BaseModel):
    """One image the composer attached, as a data URL."""

    name: str = Field(default="image", max_length=200)
    data_url: str = Field(min_length=1, max_length=15_000_000)


def store_uploads(uploads: list[AttachmentUpload]) -> list[dict]:
    """Save a message's images; a bad one refuses the whole message with a 400."""
    if len(uploads) > MAX_ATTACHMENTS:
        raise HTTPException(400, f"Attach at most {MAX_ATTACHMENTS} images to one message.")
    try:
        return save_attachments([(item.name, item.data_url) for item in uploads])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/chat-attachments/{attachment_id}")
def serve_attachment(attachment_id: str, size: int | None = None):
    path = attachment_path(attachment_id)
    if path is None or not path.is_file():
        raise HTTPException(404, "Attachment not found")
    # The chat grid is a few dozen pixels. The original stays for the lightbox.
    if size is not None:
        if size not in THUMBNAIL_SIZES:
            raise HTTPException(400, "Unsupported thumbnail size")
        try:
            thumb = attachment_thumbnail(path, size)
        except Exception:
            raise HTTPException(404, "Attachment not found") from None
        return FileResponse(
            str(thumb),
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=86400"},
        )
    # Ids are random and never reused, so a browser may keep the image.
    return FileResponse(
        str(path),
        media_type=STORED_MIME[path.suffix],
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )
