"""The seller's inventory labels across every item: count, rename and delete.

A label is a string an item wears, kept in the item's notes as
``vendooLabels`` (comma-separated), which is what the inventory filters and
Item Details read and edit. Labels are private: changing them saves no
listing revision, so a live item never looks edited because of one.
Matching ignores case, as the inventory's label filter does.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from anteroom.models.conversation import Conversation
from anteroom.repositories.queries import ConversationRepo
from anteroom.services.vendoo_import import merge_notes, parse_notes, split_vendoo_labels

MAX_LABEL_LENGTH = 100
_LISTED_IDS = 100


class LabelError(ValueError):
    """A label change the seller has to correct."""


def item_labels(notes: dict[str, Any], listing: dict[str, Any] | None) -> list[str]:
    """An item's labels: the notes' copy, which the inventory edits, else the revision's own."""
    if "vendooLabels" in notes:
        return split_vendoo_labels(notes.get("vendooLabels"))
    raw = (listing or {}).get("labels")
    return [str(label).strip() for label in raw if str(label).strip()] if isinstance(raw, list) else []


def _clean(label: Any, name: str) -> str:
    text = " ".join(str(label or "").split())
    if not text:
        raise LabelError(f"{name} must not be blank.")
    if len(text) > MAX_LABEL_LENGTH:
        raise LabelError(f"{name} must be at most {MAX_LABEL_LENGTH} characters.")
    if "," in text:
        raise LabelError(f"{name} cannot contain a comma.")
    return text


def _labelled(db: Session, *, lock: bool = False) -> list[tuple[Conversation, list[str]]]:
    query = select(Conversation).where(Conversation.notes.contains("vendooLabels")).order_by(Conversation.id)
    if lock:
        query = query.with_for_update()
    rows = []
    for conv in db.scalars(query).all():
        labels = split_vendoo_labels(parse_notes(conv.notes).get("vendooLabels"))
        if labels:
            rows.append((conv, labels))
    return rows


def label_counts(db: Session) -> list[dict[str, Any]]:
    """Every label in the inventory with how many items wear it, by name.

    Spellings that differ only in case are one label, named as most items spell it.
    """
    items: dict[str, int] = {}
    spellings: dict[str, dict[str, int]] = {}
    for _conv, labels in _labelled(db):
        for label in {label.casefold(): label for label in reversed(labels)}.values():
            key = label.casefold()
            items[key] = items.get(key, 0) + 1
            counts = spellings.setdefault(key, {})
            counts[label] = counts.get(label, 0) + 1
    named = [
        {"label": max(spellings[key].items(), key=lambda entry: entry[1])[0], "items": count}
        for key, count in items.items()
    ]
    return sorted(named, key=lambda row: (row["label"].casefold(), row["label"]))


def _rewrite(db: Session, label: str, replacement: str | None) -> dict[str, Any]:
    """Replace (or with ``None`` remove) ``label`` on every item, in the caller's one transaction."""
    target = _clean(label, "label").casefold()
    repo = ConversationRepo(db)
    changed: list[str] = []
    for conv, labels in _labelled(db, lock=True):
        if not any(existing.casefold() == target for existing in labels):
            continue
        updated: list[str] = []
        for existing in labels:
            value = replacement if existing.casefold() == target else existing
            if value is not None and value.casefold() not in {kept.casefold() for kept in updated}:
                updated.append(value)
        # A bulk relabel is bookkeeping, not activity on each item: keep their place in the sidebar.
        repo.write_notes(conv.id, merge_notes(conv.notes, {"vendooLabels": ", ".join(updated)}),
                         bump_updated_at=False, commit=False)
        changed.append(conv.id)
    if not changed:
        raise LabelError(f"No item has the label {label!r}.")
    db.commit()
    return {"items_changed": len(changed), "conversation_ids": changed[:_LISTED_IDS]}


def rename_label(db: Session, label: str, new_label: str) -> dict[str, Any]:
    """Rename a label on every item that wears it; an item already wearing the new name keeps one."""
    replacement = _clean(new_label, "new_label")
    return {"label": _clean(label, "label"), "new_label": replacement, **_rewrite(db, label, replacement)}


def delete_label(db: Session, label: str) -> dict[str, Any]:
    """Take a label off every item that wears it. The items themselves are untouched."""
    return {"label": _clean(label, "label"), "deleted": True, **_rewrite(db, label, None)}
