"""Immutable photo files owned by one approved direct marketplace job."""
from __future__ import annotations

import contextvars
import hashlib
from concurrent.futures import ThreadPoolExecutor, wait
from types import SimpleNamespace

from vendoo_studio.services.photo_store import photo_store

KEY = "_approved_photos"


# Photos copied at once. Each is a download and an upload on the web.
COPY_WORKERS = 4


def _copy(photo) -> dict:
    contents = photo_store().read(photo.stored_filename)
    checksum = hashlib.sha256(contents).hexdigest()
    if photo.checksum and checksum != photo.checksum:
        raise ValueError("A photo changed while it was being approved. Review the photos again.")
    # A new key beside the source, so it lives in the seller's own Storage folder on the web.
    key = photo_store().edited_key(photo.stored_filename)
    photo_store().put(key, contents)
    return {"id": photo.id, "stored_filename": key, "mime_type": photo.mime_type,
            "checksum": checksum, "size_bytes": len(contents)}


def capture(photos: list) -> list[dict]:
    """Copy the selected bytes before dispatch; later edits keep their own files."""
    with ThreadPoolExecutor(max_workers=COPY_WORKERS) as pool:
        # Each copy runs in the seller's context, which picks their Storage folder.
        futures = [pool.submit(contextvars.copy_context().run, _copy, photo) for photo in photos]
        wait(futures)
    captured = [future.result() for future in futures if future.exception() is None]
    errors = [error for future in futures if (error := future.exception()) is not None]
    if errors:
        release({KEY: captured})
        raise errors[0]
    return captured


def load(snapshot: dict) -> list[SimpleNamespace]:
    rows = snapshot.get(KEY)
    if not isinstance(rows, list):
        raise ValueError("This action has no approved photos. Review the listing before starting a new action.")
    for row in rows:
        if not photo_store().exists(row["stored_filename"]):
            raise ValueError("An approved photo is missing. Review the photos before starting a new action.")
    return [SimpleNamespace(**row) for row in rows]


def release(snapshot: dict, *, keep: set[str] | None = None) -> None:
    """Delete a job's approved copies. Every row is a copy ``capture`` made, never a source photo."""
    for row in snapshot.get(KEY) or []:
        key = row.get("stored_filename", "")
        if key and (not keep or key not in keep):
            photo_store().delete(key)
