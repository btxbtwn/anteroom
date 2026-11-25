"""``read_size_tag`` and ``read_measurements``: Item Details' read buttons, for AI assistants.

Each runs the same reader the buttons use (``size_tag_read``, ``measure_read``)
on the listing's saved photos, in their order. The handler only checks the
listing and gathers its photo keys inside the call's database session; the
photos are fetched and read after that session closes (``routes/mcp.py``),
because a reading waits on a photo model. With ``save`` (the default) the
reading then fills Item Details the way the buttons' Fill does: a size or a
measurement already there always stays, only blanks take a reading, and a
missing, unreadable or conflicting tag is reported, never saved. Generation
reads Item Details, so the listing it writes uses them word for word.

Nothing here reaches a marketplace.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from time import monotonic
from typing import Any

from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from anteroom.models.conversation import Conversation
from anteroom.repositories.queries import ConversationRepo, ListingRepo
from anteroom.services.general_measurements import (
    GARMENT_SPECS,
    active_garment,
    normalize_garment,
    note_changes_for_measurement_updates,
    stored_measurements,
)
from anteroom.services.seller_overrides import is_locked
from anteroom.services.vendoo_import import merge_note_changes, parse_notes

log = logging.getLogger(__name__)

# A reading asks a photo model twice (three times for a split measurement), so
# these calls get longer than the MCP's usual 30 s. The app's own read buttons
# wait as long as the model takes; every provider call keeps its own deadline.
CALL_DEADLINE_SECONDS = 120.0
_PLACEHOLDER = "_anteroom_photo_reading"
_GARMENTS = [garment for garment, _label, _fields in GARMENT_SPECS]
_FIELDS = {garment: fields for garment, _label, fields in GARMENT_SPECS}

_CONVERSATION_SCHEMA = {"type": "string", "description": "Listing / conversation ID."}
_SAVE_SCHEMA = {
    "type": "boolean",
    "default": True,
    "description": "Fill blank Item Details fields with what was read (default true). false only reports the reading.",
}
_ANNOTATIONS = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "read_size_tag",
        "title": "Read the size tag",
        "description": (
            "Read the clothing size off the size tag in a listing's saved photos, like Read size tags in "
            "Item Details. Two independent reads must agree; otherwise the status is not_found, unreadable "
            "or conflict and nothing is saved. With save (default), an agreed size fills Item Details Size "
            "only when it is blank: a size already set stays, and the result says when the tag disagrees "
            "with it. Generation uses Item Details Size. Run it before generating. Can take up to two "
            "minutes. Never reaches a marketplace."
        ),
        "annotations": _ANNOTATIONS,
        "inputSchema": {
            "type": "object",
            "properties": {"conversation_id": _CONVERSATION_SCHEMA, "save": _SAVE_SCHEMA},
            "required": ["conversation_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_measurements",
        "title": "Read measurements",
        "description": (
            "Read the measurements from the yardstick photos among a listing's saved photos, like Read "
            "measurements in Item Details: top (pitToPit, length, sleeve), pants or shorts (waist, rise, "
            "inseam, legOpening) or skirt (waist, hip, length), in inches. A value is accepted only when "
            "independent reads agree. With save (default), accepted values fill only blank Item Details "
            "measurements for that garment; the result lists the ones to measure by hand. Generation "
            "writes Item Details measurements into the description word for word. Run it before "
            "generating. Can take up to two minutes. Never reaches a marketplace."
        ),
        "annotations": _ANNOTATIONS,
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": _CONVERSATION_SCHEMA,
                "garment": {
                    "type": "string",
                    "enum": _GARMENTS,
                    "description": "Which measurements to read. Default: the garment Item Details shows for this listing.",
                },
                "save": _SAVE_SCHEMA,
            },
            "required": ["conversation_id"],
            "additionalProperties": False,
        },
    },
]

# Saving changes the listing's Item Details, so both need Listings write access.
PERMISSIONS: dict[str, tuple[str, str]] = {
    "read_size_tag": ("listings", "write"),
    "read_measurements": ("listings", "write"),
}


class ReadingToolError(ValueError):
    """A refusal the agent reads, as an MCP tool error."""


# --- in the call's database session -------------------------------------------


def _conversation_id(args: dict[str, Any]) -> str:
    value = args.get("conversation_id")
    if not isinstance(value, str) or not value.strip():
        raise ReadingToolError("conversation_id is required.")
    return value.strip()


def _save(args: dict[str, Any]) -> bool:
    value = args.get("save", True)
    if not isinstance(value, bool):
        raise ReadingToolError("save must be true or false.")
    return value


def _photo_keys(db: Session, conv_id: str) -> list[str]:
    repo = ConversationRepo(db)
    if repo.get(conv_id) is None:
        raise ReadingToolError("Listing not found.")
    keys = [photo.stored_filename for photo in repo.get_photos(conv_id)]
    if not keys:
        raise ReadingToolError("This listing has no photos to read. Add its photos first.")
    return keys


def _photo_provider(what: str):
    from anteroom.services.listing_provider import get_photo_provider

    provider = get_photo_provider()
    if provider is None or not callable(getattr(provider, "vision_chat", None)):
        raise ReadingToolError(f"Connect a model in Settings to read {what} from photos.")
    return provider


def _current_listing(db: Session, conv_id: str) -> dict[str, Any]:
    repo = ListingRepo(db)
    current = repo.get_current(conv_id)
    revision = repo.get_revision(current.current_revision_id) if current and current.current_revision_id else None
    return revision.listing_json if revision is not None and isinstance(revision.listing_json, dict) else {}


def _read_size_tag(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    conv_id = _conversation_id(args)
    save = _save(args)
    keys = _photo_keys(db, conv_id)
    provider = _photo_provider("size tags")
    return {"_after": [{_PLACEHOLDER: {
        "kind": "size_tag", "conversation_id": conv_id, "save": save, "keys": keys, "provider": provider,
    }}]}


def _read_measurements(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    from anteroom.services.listing_provider import managed_task_provider

    conv_id = _conversation_id(args)
    save = _save(args)
    garment = None
    if args.get("garment") is not None:
        garment = normalize_garment(args.get("garment"))
        if garment is None:
            raise ReadingToolError(f"garment must be one of: {', '.join(_GARMENTS)}.")
    keys = _photo_keys(db, conv_id)
    if garment is None:
        conv = ConversationRepo(db).get(conv_id)
        garment = active_garment(_current_listing(db, conv_id), parse_notes(conv.notes if conv else None))
    provider = managed_task_provider("measurement") or _photo_provider("measurements")
    return {"_after": [{_PLACEHOLDER: {
        "kind": "measurements", "conversation_id": conv_id, "save": save, "keys": keys,
        "garment": garment, "provider": provider,
    }}]}


HANDLERS = {
    "read_size_tag": _read_size_tag,
    "read_measurements": _read_measurements,
}


# --- after the session closes -------------------------------------------------


def call_timeout(payload: Any, default: float) -> float:
    """The deadline for one MCP request: longer when it calls a reading tool."""
    calls = payload if isinstance(payload, list) else [payload]
    for call in calls:
        params = call.get("params") if isinstance(call, dict) and call.get("method") == "tools/call" else None
        if isinstance(params, dict) and params.get("name") in HANDLERS:
            return max(default, CALL_DEADLINE_SECONDS)
    return default


def pending_reads(body: Any) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """(tool result, reading) for each reading tool call that still has to read."""
    reads = []
    for response in body if isinstance(body, list) else [body]:
        result = response.get("result") if isinstance(response, dict) else None
        if isinstance(result, dict):
            for item in result.get("content", []):
                if isinstance(item, dict) and _PLACEHOLDER in item:
                    reads.append((result, item[_PLACEHOLDER]))
    return reads


async def finish_reads(reads: list[tuple[dict[str, Any], dict[str, Any]]], *, deadline: float, request=None) -> None:
    """Read each listing's photos, save what may be saved, and write the outcome into its result."""

    async def finish(result: dict[str, Any], spec: dict[str, Any]) -> None:
        try:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError
            if request is not None:
                from anteroom.services.hosted_limits import limit_seller_submissions

                # The hosted app's per-seller budget for new model work, as the buttons have.
                limit_seller_submissions(request)
            async with asyncio.timeout(remaining):
                data = await _read_and_save(spec, remaining, lambda: monotonic() >= deadline)
            result["content"] = [{"type": "text", "text": json.dumps(data, indent=2, default=str)}]
            result["isError"] = False
        except Exception as exc:  # noqa: BLE001 - each reading reports its own failure
            result["content"] = [{"type": "text", "text": _failure(spec, exc)}]
            result["isError"] = True

    await asyncio.gather(*(finish(result, spec) for result, spec in reads))


def _failure(spec: dict[str, Any], exc: Exception) -> str:
    from anteroom.services.provider_deadlines import provider_label, unwrap

    what = "size tag" if spec["kind"] == "size_tag" else "measurements"
    if isinstance(exc, TimeoutError):
        return f"Reading the {what} took too long, so nothing was saved. Try again."
    if isinstance(exc, HTTPException):
        return f"Nothing was read or saved: {exc.detail}"
    if isinstance(exc, ReadingToolError):
        return str(exc)
    if isinstance(exc, RuntimeError):
        # A provider that failed or stalled names itself (provider_deadlines.ProviderStalled).
        return f"{exc} Nothing was saved."
    log.exception("MCP %s reading failed", what, exc_info=exc)
    who = provider_label(getattr(unwrap(spec.get("provider")), "name", None))
    return f"{who} could not read the {what} in these photos. Nothing was saved."


async def _read_and_save(spec: dict[str, Any], remaining: float, expired: Callable[[], bool]) -> dict[str, Any]:
    from anteroom.database import in_worker_session
    from anteroom.services import measure_read, size_tag_read

    with tempfile.TemporaryDirectory(prefix="mcp-reading-") as workdir:
        paths = await asyncio.to_thread(_download, spec["keys"], Path(workdir), remaining)
        if spec["kind"] == "size_tag":
            reading = await size_tag_read.read_size_tag(spec["provider"], paths)
        else:
            reading = await measure_read.read_measurements(spec["provider"], paths, spec["garment"])
    conv_id = spec["conversation_id"]
    if spec["kind"] == "size_tag":
        return await in_worker_session(_settle_size, conv_id, reading, spec["save"], expired)
    return await in_worker_session(
        _settle_measurements, conv_id, spec["garment"], reading, spec["save"], expired)


def _lock_listing(db: Session, conv_id: str, expired: Callable[[], bool]) -> Conversation:
    """The listing, locked until this session ends, in a session that will not commit late.

    The lock makes the blank-field check and the notes merge one step, so a size
    and a measurement reading of one listing (``finish_reads`` runs them side by
    side) or a seller's edit cannot overwrite each other's notes. Cancelling the
    awaiting coroutine at the deadline does not stop this worker thread, so the
    commit itself refuses once the call's deadline has passed and the agent was
    told nothing was saved.
    """
    def refuse_late_commit(*_args: Any) -> None:
        if expired():
            raise TimeoutError

    event.listen(db, "before_commit", refuse_late_commit)
    # A commit flushes after before_commit, and a flush can wait on a locked database.
    event.listen(db, "after_flush", refuse_late_commit)
    refuse_late_commit()
    if db.get_bind().dialect.name == "sqlite":
        # SQLite ignores FOR UPDATE and pysqlite begins no transaction for a SELECT:
        # take the write lock before the first read so the notes merge is not stale.
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    db.scalar(select(Conversation.id).where(Conversation.id == conv_id).with_for_update())
    conv = ConversationRepo(db).get(conv_id)
    if conv is None:
        raise ReadingToolError("Listing not found.")
    return conv


def _download(keys: list[str], folder: Path, remaining: float) -> list[Path]:
    """The listing's photos, in their order, as files the readers open."""
    from anteroom.services.mcp_http import read_photo

    started = monotonic()
    paths = []
    for index, key in enumerate(keys):
        path = folder / f"{index:02d}{Path(key).suffix.lower() or '.jpg'}"
        path.write_bytes(read_photo(key, timeout=max(1.0, remaining - (monotonic() - started))))
        paths.append(path)
    return paths


def _same_size(a: str, b: str) -> bool:
    """One size however it is spaced or cased, and 32 x 30 is 32×30, as ``size_tag_read`` compares."""
    def key(size: str) -> str:
        return re.sub(r"\s*[x×]\s*", "×", " ".join(size.split()).casefold())

    return key(a) == key(b)


def _set_size(db: Session, conv_id: str) -> str:
    """The size the seller already set: Item Details Size, or a Size they set on the listing."""
    conv = ConversationRepo(db).get(conv_id)
    notes = parse_notes(conv.notes if conv else None)
    typed = str(notes.get("size") if notes.get("size") is not None else "").strip()
    if typed:
        return typed
    listing = _current_listing(db, conv_id)
    if is_locked(listing, "size") and str(listing.get("size") or "").strip():
        return str(listing.get("size")).strip()
    return ""


def _settle_size(
    db: Session, conv_id: str, reading: dict[str, Any], save: bool, expired: Callable[[], bool],
) -> dict[str, Any]:
    """Fold a size-tag reading into Item Details as the button's Fill does (``sizeTagReading.ts``)."""
    repo = ConversationRepo(db)
    conv = _lock_listing(db, conv_id, expired)
    typed = _set_size(db, conv_id)
    status, sizes = reading.get("status"), list(reading.get("sizes") or [])
    size = str(reading.get("size") or "").strip()
    accepted = status == "accepted" and size != ""
    saved = False
    if accepted and typed:
        note = ("Size matches the tag." if _same_size(typed, size) else
                f"The tag reads {size}. Item Details Size is {typed}; it was kept. Check it.")
    elif accepted and save:
        repo.write_notes(conv_id, merge_note_changes(conv.notes, {"size": size}))
        saved = True
        note = "Size saved to Item Details from the tag."
    elif accepted:
        note = "Size read from the tag. Not saved (save was false)."
    elif status == "conflict" and len(sizes) > 1:
        note = f"The photos show different sizes ({', '.join(sizes)}). Nothing was saved; check the tag."
    elif status == "not_found":
        note = "No size tag found in the photos. Nothing was saved; add a clear tag photo or set the size."
    else:
        note = "The size tag could not be read. Nothing was saved; add a clearer tag photo or set the size."
    return {
        "conversation_id": conv_id,
        "photos_read": reading.get("photos_read", 0),
        "status": status,
        "size": size if accepted else None,
        "sizes": sizes,
        "saved": saved,
        "item_details_size": size if saved else (typed or None),
        "note": note,
        "pushed_to_marketplaces": False,
    }


def _settle_measurements(
    db: Session, conv_id: str, garment: str, reading: dict[str, Any], save: bool, expired: Callable[[], bool],
) -> dict[str, Any]:
    """Fold a yardstick reading into Item Details as the button's Fill does (``measurementReading.ts``)."""
    repo = ConversationRepo(db)
    conv = _lock_listing(db, conv_id, expired)
    notes = parse_notes(conv.notes)
    typed = stored_measurements(notes)[garment]
    readings = reading.get("measurements") or {}
    fills: dict[str, str] = {}
    by_hand: list[str] = []
    for key, label in _FIELDS[garment]:
        read = readings.get(key) or {}
        if typed.get(key):
            continue
        if read.get("status") == "accepted" and read.get("value") is not None:
            fills[key] = f"{read['value']:g}" if isinstance(read["value"], float) else str(read["value"])
        else:
            by_hand.append(label)
    saved: dict[str, str] = {}
    if save and fills:
        changes, applied = note_changes_for_measurement_updates(
            [(f"measurements.{garment}.{key}", value, False) for key, value in fills.items()],
            conv.notes, _current_listing(db, conv_id),
        )
        changes["garment"] = garment
        repo.write_notes(conv_id, merge_note_changes(conv.notes, changes))
        saved = {path.rsplit(".", 1)[1]: value for path, value in applied.items() if value}
    if not reading.get("photos_read"):
        note = "No yardstick photo found. Nothing was saved."
    elif saved:
        note = "Saved to Item Details from the photos."
    elif fills:
        note = "Read from the photos. Not saved (save was false)."
    elif not any(read.get("status") == "accepted" for read in readings.values()):
        note = "The yardstick could not be read. Nothing was saved."
    else:
        note = "Nothing new could be read from the photos: Item Details already has these measurements."
    if by_hand and reading.get("photos_read"):
        note += f" Measure by hand: {', '.join(by_hand)}."
    return {
        "conversation_id": conv_id,
        "garment": garment,
        "photos_read": reading.get("photos_read", 0),
        "readings": {key: {k: v for k, v in read.items() if k in ("status", "value")} for key, read in readings.items()},
        "read": fills,
        "saved": saved,
        "item_details_measurements": {**typed, **saved},
        "measure_by_hand": by_hand if reading.get("photos_read") else [label for _key, label in _FIELDS[garment]],
        "note": note,
        "pushed_to_marketplaces": False,
    }
