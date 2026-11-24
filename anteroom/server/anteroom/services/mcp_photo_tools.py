"""MCP tools that put photos on a listing: ``add_listing_photos``, ``create_photo_upload_url``, ``set_cover_photo``.

Photos take the same path as the app's own uploads (``services/photos.py``):
the type is read from the bytes, the photo is decoded within the pixel limit,
hosted photos become upright JPEGs without EXIF in the seller's private
Storage, and Mac photos are kept as files. Storing never happens while a
database session is open: a tool call only checks the listing and returns a
placeholder; the MCP route stores the photos after the call's session closes,
then saves their rows in a second, short session (``finish_pending_uploads``).

An upload link (``create_photo_upload_url``) lets an assistant that can run
commands send one photo straight from disk with ``curl``. Each link names one
listing of one seller, works once, and expires after ``UPLOAD_TTL``. Its
secret travels in the ``X-Upload-Token`` header, only its SHA-256 digest is
kept, and the upload route accepts nothing else (``routes/mcp_photo_uploads.py``).

The server never fetches a photo from a URL an assistant gives it.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import contextvars
import hashlib
import json
import logging
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import delete, event, func, or_, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from anteroom.config import MAX_PHOTO_COUNT, MAX_PHOTO_SIZE_MB
from anteroom.database import in_worker_session
from anteroom.models.conversation import Conversation, Photo
from anteroom.models.mcp_photo_upload import McpPhotoUpload
from anteroom.repositories.queries import ConversationRepo
from anteroom.services import listing_persistence
from anteroom.services import photos as photo_pipeline
from anteroom.services.hosted_limits import hold_upload_slot, read_bounded_body
from anteroom.services.photo_store import photo_store

log = logging.getLogger(__name__)

# A tool call's body is capped at 2 MB, so a few phone photos downscaled to
# about 1600 px fit; anything larger goes through an upload link.
MAX_INLINE_PHOTOS = 5
MAX_UPLOAD_BYTES = MAX_PHOTO_SIZE_MB * 1024 * 1024
UPLOAD_TTL = timedelta(minutes=15)
# Unused, unexpired links one seller may hold at once.
MAX_ACTIVE_UPLOADS = 60
UPLOAD_TOKEN_PREFIX = "aru_"
UPLOAD_HEADER = "X-Upload-Token"
UPLOAD_PATH = "/api/mcp/photo-uploads/"
_UPLOAD_ID = re.compile(r"[0-9a-f]{32}")
ACCEPTED_TYPES = "JPEG, PNG, WebP or HEIC"
_EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/heic": ".heic"}
# Marks a tool result whose photos are stored after the call's session closes.
_UPLOAD_PLACEHOLDER = "_anteroom_photo_upload"

# Where the MCP request arrived, so an upload link points back at the same server.
request_origin: contextvars.ContextVar[str | None] = contextvars.ContextVar("mcp_request_origin", default=None)

_WRITE_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": False,
}


class PhotoToolError(ValueError):
    """A refusal the assistant is told about (the MCP server reports a ValueError as a tool error)."""


class ListingNotFound(PhotoToolError):
    pass


def _mcp():
    # mcp_http imports this module to list its tools; import it back only when called.
    from anteroom.services import mcp_http
    return mcp_http


# ---------------------------------------------------------------------------
# Reading photo bytes
# ---------------------------------------------------------------------------


def sniff_mime(data: bytes) -> str | None:
    """The photo type the bytes themselves say, whatever a client declared."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1"}:
        return "image/heic"
    return None


def _filename(given: Any, mime: str) -> str:
    """A plain file name for the photo: no folders, and the extension its real type has."""
    from anteroom.services.service_context import current

    stem = ""
    if isinstance(given, str):
        base = re.split(r"[\\/]", given.strip())[-1]
        stem = re.sub(r"[^A-Za-z0-9 _.-]", "", base.rsplit(".", 1)[0]).strip(" .")[:80]
    # Hosted photos are stored as JPEGs.
    return (stem or "photo") + (".jpg" if current() is not None else _EXTENSIONS[mime])


def _decode_inline(item: Any, number: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise PhotoToolError(f"Photo {number} must be an object with the image as base64 in data.")
    raw = item.get("data")
    if not isinstance(raw, str) or not raw.strip():
        raise PhotoToolError(f"Photo {number} needs data: the image file as base64.")
    encoded = raw.strip()
    if encoded.startswith("data:"):
        encoded = encoded.partition(",")[2]
    if len(encoded) > (MAX_UPLOAD_BYTES * 4) // 3 + 4:
        raise PhotoToolError(f"Photo {number} is larger than {MAX_PHOTO_SIZE_MB} MB.")
    try:
        data = base64.b64decode("".join(encoded.split()), validate=True)
    except (binascii.Error, ValueError):
        raise PhotoToolError(f"Photo {number}: data is not valid base64.") from None
    mime = sniff_mime(data)
    if mime is None:
        raise PhotoToolError(f"Photo {number} is not a {ACCEPTED_TYPES} photo.")
    return {"data": data, "mime": mime, "filename": _filename(item.get("filename"), mime)}


# ---------------------------------------------------------------------------
# Database steps
# ---------------------------------------------------------------------------


def _general_photo_count(db: Session, conv_id: str) -> int:
    return db.scalar(
        select(func.count()).select_from(Photo).where(Photo.conversation_id == conv_id, Photo.marketplace.is_(None))
    ) or 0


def check_room(db: Session, conv_id: str, adding: int) -> int:
    """Refuse unless the listing exists, is idle, and has room for ``adding`` more photos."""
    if ConversationRepo(db).get(conv_id) is None:
        raise ListingNotFound("Listing not found.")
    mcp_http = _mcp()
    try:
        mcp_http._assert_not_busy(db, conv_id)
    except mcp_http.McpToolError as exc:
        raise PhotoToolError(str(exc)) from None
    count = _general_photo_count(db, conv_id)
    if count + adding > MAX_PHOTO_COUNT:
        raise PhotoToolError(
            f"A listing holds at most {MAX_PHOTO_COUNT} photos and this one has {count}. "
            "Remove some with manage_listing_photos first."
        )
    return count


def _refuse_late_commit(db: Session, expired: Callable[[], bool]) -> None:
    def check(*_args) -> None:
        if expired():
            raise TimeoutError
    event.listen(db, "before_commit", check)
    event.listen(db, "after_flush", check)


def save_photos(db: Session, conv_id: str, metas: list[dict[str, Any]], expired: Callable[[], bool]) -> tuple[list[str], int]:
    """Add stored photos to the listing, all in one commit, after checking again that they fit."""
    _refuse_late_commit(db, expired)
    # Lock the listing, so two uploads at once cannot pass the photo limit together.
    db.scalar(select(Conversation.id).where(Conversation.id == conv_id).with_for_update())
    count = check_room(db, conv_id, len(metas))
    added = listing_persistence.add_photos(db, conv_id, metas)
    return [photo.id for photo in added], count + len(added)


def _discard(keys: list[str]) -> None:
    store = photo_store()
    for key in keys:
        try:
            store.delete(key)
        except Exception:  # noqa: BLE001 - only an unreachable private file remains
            log.warning("A photo that was not saved stayed in storage")


async def _store(conv_id: str, data: bytes, filename: str, mime: str) -> dict[str, Any]:
    """Normalize and store one photo as the app's own upload does. Call with no session open."""
    return await asyncio.to_thread(photo_pipeline.process_bytes, data, filename, mime, collection_id=conv_id)


async def _save(conv_id: str, metas: list[dict[str, Any]], expired: Callable[[], bool]) -> tuple[list[str], int]:
    """Save the stored photos' rows; if they are not saved, remove the stored files."""
    try:
        return await in_worker_session(save_photos, conv_id, metas, expired)
    except BaseException:
        await asyncio.shield(asyncio.to_thread(_discard, [meta["stored_filename"] for meta in metas]))
        raise


# ---------------------------------------------------------------------------
# add_listing_photos
# ---------------------------------------------------------------------------


def _tool_add_listing_photos(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    conv_id = _mcp()._require_str(args, "conversation_id")
    raw = args.get("photos")
    if not isinstance(raw, list) or not raw:
        raise PhotoToolError("photos must list at least one photo.")
    if len(raw) > MAX_INLINE_PHOTOS:
        raise PhotoToolError(
            f"Send at most {MAX_INLINE_PHOTOS} photos per call. For more photos, or large ones, "
            "use create_photo_upload_url."
        )
    decoded = [_decode_inline(item, number) for number, item in enumerate(raw, 1)]
    check_room(db, conv_id, len(decoded))
    # Stored by the MCP route once this call's session is closed (finish_pending_uploads).
    return {"_images": [{"type": "text", "text": "", _UPLOAD_PLACEHOLDER: {"conversation_id": conv_id, "photos": decoded}}]}


def pending_uploads(body: Any) -> list[dict[str, Any]]:
    """The tool results in a response that still hold photos to store."""
    responses = body if isinstance(body, list) else [body]
    found = []
    for response in responses:
        result = response.get("result") if isinstance(response, dict) else None
        content = result.get("content") if isinstance(result, dict) else None
        if isinstance(content, list) and any(isinstance(item, dict) and _UPLOAD_PLACEHOLDER in item for item in content):
            found.append(result)
    return found


async def finish_pending_uploads(
    pending: list[dict[str, Any]], *, expired: Callable[[], bool], request: Request | None = None,
) -> None:
    """Store each result's photos and save them, then replace the result with what happened."""
    for result in pending:
        job = next(item[_UPLOAD_PLACEHOLDER] for item in result["content"]
                   if isinstance(item, dict) and _UPLOAD_PLACEHOLDER in item)
        try:
            outcome = await _store_and_save(job["conversation_id"], job["photos"], expired=expired, request=request)
            result["content"] = [{"type": "text", "text": json.dumps(outcome, indent=2)}]
            result["isError"] = False
        except PhotoToolError as exc:
            result["content"] = [{"type": "text", "text": str(exc)}]
            result["isError"] = True


async def _store_and_save(
    conv_id: str, photos: list[dict[str, Any]], *, expired: Callable[[], bool], request: Request | None,
) -> dict[str, Any]:
    metas: list[dict[str, Any]] = []
    failed: list[str] = []
    # The hosted API bounds how many photos decode at once, per seller, as for the web app's uploads.
    async with hold_upload_slot(request, per_seller=True):
        for number, photo in enumerate(photos, 1):
            if expired():
                failed.append(f"Photo {number} was not saved: the request ran out of time.")
                continue
            try:
                metas.append(await _store(conv_id, photo["data"], photo["filename"], photo["mime"]))
            except ValueError as exc:
                failed.append(f"Photo {number}: {exc}.")
            except HTTPException as exc:
                failed.append(f"Photo {number} could not be stored: {exc.detail}")
    if not metas:
        raise PhotoToolError("No photo was added. " + " ".join(failed))
    try:
        added, count = await _save(conv_id, metas, expired)
    except TimeoutError:
        raise PhotoToolError(
            "The photos were not saved: the request ran out of time. Send fewer or smaller photos, "
            "or use create_photo_upload_url."
        ) from None
    return {
        "ok": True,
        "conversation_id": conv_id,
        "added_photo_ids": added,
        "failed": failed,
        "photo_count": count,
        "published": False,
        "pushed_to_marketplaces": False,
        "note": "Added to the local listing only. Use set_cover_photo to choose the first photo.",
    }


# ---------------------------------------------------------------------------
# create_photo_upload_url
# ---------------------------------------------------------------------------


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def origin_for(request: Request, *, hosted: bool) -> str:
    """The origin the assistant reached, for an upload link back to the same server."""
    from anteroom.services.local_requests import came_through_tailnet
    from anteroom.services.mcp_oauth import public_origin

    if hosted or came_through_tailnet(request.headers):
        origin = public_origin(request.headers.get("host"))
        if origin:
            return origin
    return str(request.base_url).rstrip("/")


def _curl(url: str, secret: str) -> str:
    return (
        f"curl -sS -X POST -H '{UPLOAD_HEADER}: {secret}' -H 'Content-Type: application/octet-stream' "
        f"--data-binary @\"/path/to/photo.jpg\" '{url}'"
    )


def _tool_create_photo_upload_url(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    conv_id = _mcp()._require_str(args, "conversation_id")
    count = args.get("count", 1)
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_PHOTO_COUNT:
        raise PhotoToolError(f"count must be 1 to {MAX_PHOTO_COUNT}: a listing holds at most {MAX_PHOTO_COUNT} photos.")
    check_room(db, conv_id, count)
    now = datetime.now(UTC)
    # Spent and expired links are no use to anyone: clear this seller's away.
    db.execute(delete(McpPhotoUpload).where(or_(McpPhotoUpload.used_at.is_not(None), McpPhotoUpload.expires_at <= now)))
    db.commit()
    active = db.scalar(select(func.count()).select_from(McpPhotoUpload)) or 0
    if active + count > MAX_ACTIVE_UPLOADS:
        raise PhotoToolError(
            f"You already have {active} unused upload links. Use them, or wait up to "
            f"{int(UPLOAD_TTL.total_seconds() // 60)} minutes for them to expire."
        )
    expires_at = now + UPLOAD_TTL
    origin = request_origin.get() or ""
    uploads = []
    for _ in range(count):
        upload_id = secrets.token_hex(16)
        secret = UPLOAD_TOKEN_PREFIX + secrets.token_urlsafe(32)
        db.add(McpPhotoUpload(id=upload_id, conversation_id=conv_id, token_hash=_hash(secret),
                              created_at=now, expires_at=expires_at))
        url = f"{origin}{UPLOAD_PATH}{upload_id}"
        uploads.append({"upload_url": url, "upload_token": secret, "curl": _curl(url, secret)})
    db.commit()
    return {
        "conversation_id": conv_id,
        "uploads": uploads,
        "expires_at": expires_at.isoformat(),
        "expires_in_seconds": int(UPLOAD_TTL.total_seconds()),
        "max_bytes": MAX_UPLOAD_BYTES,
        "accepted_types": ACCEPTED_TYPES,
        "instructions": (
            "Each link uploads one photo, once. Run its curl command with /path/to/photo.jpg replaced by the "
            f"photo's path on this computer, or POST the file's bytes to upload_url with the {UPLOAD_HEADER} "
            "header. Photos are added in the order they are uploaded. A failed upload spends its link: ask for "
            "a new one. Keep the upload token private; it is not shown again."
        ),
        "published": False,
    }


@dataclass(frozen=True)
class UploadClaim:
    owner_id: str
    conversation_id: str


def consume_upload(engine: Engine, upload_id: str, secret: str, *, hosted: bool) -> UploadClaim | None:
    """Spend a link, once, if ``secret`` is its token and it has not expired; None otherwise."""
    if (not _UPLOAD_ID.fullmatch(upload_id or "") or not isinstance(secret, str)
            or not secret.startswith(UPLOAD_TOKEN_PREFIX) or not 20 <= len(secret) <= 128):
        return None
    digest = _hash(secret)
    if hosted:
        # No seller is known before the link is spent; row security hides the
        # table, so a narrow function spends it and names its seller.
        if engine.dialect.name != "postgresql":
            return None
        from sqlalchemy import text

        with engine.begin() as connection:
            row = connection.execute(
                text("SELECT owner_id, listing_id FROM anteroom_private.consume_mcp_photo_upload(:id, :hash)"),
                {"id": upload_id, "hash": digest},
            ).first()
        return UploadClaim(str(row.owner_id), str(row.listing_id)) if row is not None else None
    now = datetime.now(UTC)
    row = McpPhotoUpload
    with engine.begin() as connection:
        spent = connection.execute(
            update(row)
            .where(row.id == upload_id, row.token_hash == digest, row.used_at.is_(None), row.expires_at > now)
            .values(used_at=now)
        )
        if (spent.rowcount or 0) != 1:
            return None
        owner, conv_id = connection.execute(
            select(row.user_id, row.conversation_id).where(row.id == upload_id)
        ).one()
    return UploadClaim(str(owner), str(conv_id))


def _never_expired() -> bool:
    return False


async def receive_upload(request: Request, conv_id: str) -> dict[str, Any]:
    """Store the request's photo on the claimed listing. Runs in that seller's context."""
    try:
        await in_worker_session(check_room, conv_id, 1)
        async with hold_upload_slot(request, per_seller=True):
            data = bytes(await read_bounded_body(
                request, MAX_UPLOAD_BYTES,
                too_large_detail=f"Choose a photo of {MAX_PHOTO_SIZE_MB} MB or smaller.",
            ))
            mime = sniff_mime(data)
            if mime is None:
                raise HTTPException(422, f"Send a {ACCEPTED_TYPES} photo.")
            try:
                meta = await _store(conv_id, data, _filename(None, mime), mime)
            except ValueError as exc:
                raise HTTPException(422, f"{exc}. Send a {ACCEPTED_TYPES} photo.") from None
            del data
        added, count = await _save(conv_id, [meta], _never_expired)
    except ListingNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except PhotoToolError as exc:
        raise HTTPException(409, str(exc)) from None
    return {
        "ok": True,
        "conversation_id": conv_id,
        "photo_id": added[0],
        "photo_count": count,
        "published": False,
    }


# ---------------------------------------------------------------------------
# set_cover_photo
# ---------------------------------------------------------------------------


def _tool_set_cover_photo(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    mcp_http = _mcp()
    conv_id = mcp_http._require_str(args, "conversation_id")
    photo_id = mcp_http._require_str(args, "photo_id")
    if ConversationRepo(db).get(conv_id) is None:
        raise ListingNotFound("Listing not found.")
    mcp_http._assert_not_busy(db, conv_id)
    ids = list(db.scalars(
        select(Photo.id)
        .where(Photo.conversation_id == conv_id, Photo.marketplace.is_(None))
        .order_by(Photo.display_order, Photo.created_at, Photo.id)
    ).all())
    if photo_id not in ids:
        raise PhotoToolError("Photo not found among this listing's photos. get_listing lists their IDs.")
    ordered = [photo_id, *(other for other in ids if other != photo_id)]
    listing_persistence.reorder_photos(db, conv_id, ordered)
    return {
        "ok": True,
        "conversation_id": conv_id,
        "cover_photo_id": photo_id,
        "ordered_photo_ids": ordered,
        "pushed_to_marketplaces": False,
    }


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


TOOLS: list[dict[str, Any]] = [
    {
        "name": "add_listing_photos",
        "title": "Add listing photos",
        "description": (
            f"Add up to {MAX_INLINE_PHOTOS} photos to a listing, sent inline as base64 ({ACCEPTED_TYPES}). "
            "The whole request must stay under 2 MB, so downscale photos to about 1600 px first; for "
            "larger photos, or when you can run commands, use create_photo_upload_url instead. Photos are "
            "added after the existing ones; use set_cover_photo to choose the first. Never published."
        ),
        "annotations": _WRITE_ANNOTATIONS,
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string", "description": "Listing / conversation ID."},
                "photos": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_INLINE_PHOTOS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "data": {"type": "string", "description": "The image file, base64-encoded (a data: URL also works)."},
                            "filename": {"type": "string", "maxLength": 200},
                            "mime_type": {"type": "string", "description": "Optional; the type is read from the image itself."},
                        },
                        "required": ["data"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["conversation_id", "photos"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_photo_upload_url",
        "title": "Create photo upload link",
        "description": (
            "For assistants that can run commands (Claude Code, Codex, Cursor): get single-use links to upload "
            "photos from this computer to a listing, each with a ready curl command. Each link takes one "
            f"{ACCEPTED_TYPES} photo up to {MAX_PHOTO_SIZE_MB} MB and expires in "
            f"{int(UPLOAD_TTL.total_seconds() // 60)} minutes. Never published."
        ),
        "annotations": _WRITE_ANNOTATIONS,
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string", "description": "Listing / conversation ID."},
                "count": {
                    "type": "integer", "minimum": 1, "maximum": MAX_PHOTO_COUNT, "default": 1,
                    "description": "How many photos you will upload: one link each.",
                },
            },
            "required": ["conversation_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "set_cover_photo",
        "title": "Set cover photo",
        "description": (
            "Make one of a listing's photos the first (cover) photo, keeping the others in order. "
            "Does not push changes to live marketplaces."
        ),
        "annotations": _WRITE_ANNOTATIONS,
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string", "description": "Listing / conversation ID."},
                "photo_id": {"type": "string", "description": "Photo ID from get_listing."},
            },
            "required": ["conversation_id", "photo_id"],
            "additionalProperties": False,
        },
    },
]

PERMISSIONS: dict[str, tuple[str, str]] = {tool["name"]: ("listings", "write") for tool in TOOLS}

HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "add_listing_photos": _tool_add_listing_photos,
    "create_photo_upload_url": _tool_create_photo_upload_url,
    "set_cover_photo": _tool_set_cover_photo,
}
