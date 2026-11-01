"""Immutable photo files owned by one approved direct marketplace job."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

from vendoo_studio.services.photo_store import photo_store

KEY = "_approved_photos"


def capture(photos: list) -> list[dict]:
    """Copy the selected bytes before dispatch; later edits keep their own files."""
    captured: list[dict] = []
    try:
        for photo in photos:
            contents = photo_store().read(photo.stored_filename)
            checksum = hashlib.sha256(contents).hexdigest()
            if photo.checksum and checksum != photo.checksum:
                raise ValueError("A photo changed while it was being approved. Review the photos again.")
            # A new key beside the source, so it lives in the seller's own Storage folder on the web.
            key = photo_store().edited_key(photo.stored_filename)
            row = {"id": photo.id, "stored_filename": key, "mime_type": photo.mime_type,
                   "checksum": checksum, "size_bytes": len(contents)}
            captured.append(row)
            photo_store().put(key, contents)
    except BaseException:
        release({KEY: captured})
        raise
    return captured


def load(snapshot: dict) -> list[SimpleNamespace]:
    rows = snapshot.get(KEY)
    if not isinstance(rows, list) or not rows:
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
