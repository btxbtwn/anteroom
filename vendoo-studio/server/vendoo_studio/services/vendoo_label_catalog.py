"""Persistent Vendoo inventory-label id → display-name map.

A Vendoo item's ``labels`` array is Firestore ids; Anteroom stores names.
This cache is filled whenever Anteroom learns a mapping (catalog read,
``labelDetails`` on an imported item, name→id resolve for save), and
``convert_stored_labels`` uses it to turn the ids already stored into names
once. Nothing reads it to display a label: it exists for the Vendoo import and
Send to Vendoo, and goes with them.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("vendoo_studio.vendoo_label_catalog")

CATALOG_FILE = "vendoo-label-catalog.json"
# Vendoo mints label ids the same way as item ids: 20 chars, A–Z a–z 0–9.
_FIRESTORE_ID_RE = re.compile(r"^[A-Za-z0-9]{20}$")

_lock = threading.Lock()
_memory: dict[str, str] | None = None


def catalog_path() -> Path:
    from vendoo_studio.config import DATA_DIR

    return Path(DATA_DIR) / CATALOG_FILE


def looks_like_label_id(value: str) -> bool:
    """True for an opaque Vendoo label id, not a seller-facing name.

    Ids are random mixed case; a 20-letter name such as ``dirtywhitesportstees``
    is one case only.
    """
    text = str(value or "").strip()
    return bool(_FIRESTORE_ID_RE.fullmatch(text)) and text != text.lower() and text != text.upper()


def _read_disk() -> dict[str, str]:
    path = catalog_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("Vendoo label catalog unreadable: %s", exc)
        return {}
    by_id: dict[str, str] = {}
    if isinstance(data, dict):
        raw = data.get("by_id") if isinstance(data.get("by_id"), dict) else data
        if isinstance(raw, dict):
            for key, value in raw.items():
                label_id = str(key or "").strip()
                name = str(value or "").strip()
                if label_id and name:
                    by_id[label_id] = name
    return by_id


def _write_disk(by_id: dict[str, str]) -> None:
    path = catalog_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"by_id": by_id}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("Vendoo label catalog not saved: %s", exc)


def load() -> dict[str, str]:
    """id → name. Empty when nothing has been learned yet."""
    global _memory
    with _lock:
        if _memory is None:
            _memory = _read_disk()
        return dict(_memory)


def remember(pairs: dict[str, str] | list[dict[str, Any]] | None) -> dict[str, str]:
    """Merge newly learned id→name pairs onto disk. Returns the full catalog."""
    global _memory
    incoming: dict[str, str] = {}
    if isinstance(pairs, dict):
        for key, value in pairs.items():
            label_id = str(key or "").strip()
            name = str(value or "").strip()
            if label_id and name and label_id != name:
                incoming[label_id] = name
    elif isinstance(pairs, list):
        for row in pairs:
            if not isinstance(row, dict):
                continue
            label_id = str(row.get("id") or "").strip()
            name = str(
                row.get("name") or row.get("displayName") or row.get("label") or ""
            ).strip()
            if label_id and name and label_id != name:
                incoming[label_id] = name
    if not incoming:
        return load()

    with _lock:
        current = dict(_memory) if _memory is not None else _read_disk()
        merged = {**current, **incoming}
        if merged != current:
            _write_disk(merged)
        _memory = merged
        return dict(merged)


def apply(labels: list[Any], names: dict[str, str] | None = None) -> list[str]:
    """Replace ids with names where known; unknown values pass through."""
    catalog = names if names is not None else load()
    cleaned = [str(label).strip() for label in (labels or []) if str(label).strip()]
    return [catalog.get(label, label) for label in cleaned]


def convert_stored_labels(db, names: dict[str, str] | None = None, *, dry_run: bool = False) -> dict[str, Any]:
    """Rewrite every stored Vendoo label id as its name, so labels are Anteroom's own.

    Covers Item Details (``notes.vendooLabels``), every listing revision, and
    the label colors and recent labels in settings. Unknown ids stay as they
    are and are reported, so no label is dropped. Running it again changes
    nothing that is already a name.
    """

    from vendoo_studio.models.conversation import Conversation
    from vendoo_studio.models.listing import ListingRevision
    from vendoo_studio.services import user_settings
    from vendoo_studio.services.vendoo_import import merge_notes, parse_notes, split_vendoo_labels

    catalog = load() if names is None else names
    unknown: set[str] = set()
    converted = 0
    items: set[str] = set()

    def rename(labels: list[Any]) -> list[str] | None:
        nonlocal converted
        out: list[str] = []
        changed = False
        for raw in labels:
            label = str(raw).strip()
            name = catalog.get(label, "")
            if name and name != label:
                converted += 1
                changed = True
                label = name
            elif looks_like_label_id(label):
                unknown.add(label)
            out.append(label)
        if not changed:
            return None
        # An id and its name on the same item become one label.
        unique: dict[str, str] = {}
        for label in out:
            unique.setdefault(label.casefold(), label)
        return list(unique.values())

    for conv in db.query(Conversation).filter(Conversation.notes.like("%vendooLabels%")):
        renamed = rename(split_vendoo_labels(parse_notes(conv.notes).get("vendooLabels")))
        if renamed is not None:
            items.add(conv.id)
            if not dry_run:
                conv.notes = merge_notes(conv.notes, {"vendooLabels": ", ".join(renamed)})
    # A JSON array's length is written differently on SQLite and Postgres, so
    # the revisions that carry labels are picked here instead of in SQL.
    for revision in db.query(ListingRevision):
        data = revision.listing_json if isinstance(revision.listing_json, dict) else {}
        if not data.get("labels"):
            continue
        renamed = rename(data.get("labels") or [])
        if renamed is not None:
            items.add(revision.conversation_id)
            if not dry_run:
                revision.listing_json = {**data, "labels": renamed}
    if not dry_run:
        db.commit()

    if not dry_run:
        prefs = user_settings.get_ui_prefs()
        colors = dict(prefs[user_settings.LABEL_COLORS_KEY])
        named = {label.casefold() for label in colors}
        recolored: dict[str, str] = {}
        for label, color in colors.items():
            name = catalog.get(label) or label
            # A color the seller gave the name itself wins over the id's.
            if name != label and name.casefold() in named:
                continue
            recolored[name] = color
        recent = list(prefs[user_settings.RECENT_LABELS_KEY])
        renamed_recent = list(dict.fromkeys(catalog.get(label) or label for label in recent))
        if recolored != colors or renamed_recent != recent:
            user_settings.set_ui_prefs(
                label_colors=recolored if recolored != colors else None,
                recent_vendoo_labels=renamed_recent if renamed_recent != recent else None,
            )
    return {"items": len(items), "converted": converted, "unknown": sorted(unknown)}


def clear_for_tests() -> None:
    """Drop the in-memory + on-disk catalog. Tests only."""
    global _memory
    with _lock:
        _memory = {}
        path = catalog_path()
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass
