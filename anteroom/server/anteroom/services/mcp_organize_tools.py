"""MCP tools for tidying the inventory: unsent edits, labels and duplicate decisions.

``services/mcp_http.py`` lists these beside its own tools. Like every MCP tool
they never contact a marketplace: discarding unsent edits puts the local item
back to what its marketplaces already show, and labels and duplicate decisions
are private to Anteroom.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from anteroom.services import home_dashboard, inventory_labels, pending_edits, similar_items
from anteroom.services.hosted_listing_contract import MARKETS, stored_listing_fields

MAX_PENDING_ITEMS = 50

_READ = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
_WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
_DESTRUCTIVE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False}

_ID = {"type": "string", "maxLength": 200}
_LABEL = {"type": "string", "minLength": 1, "maxLength": inventory_labels.MAX_LABEL_LENGTH}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_pending_edits",
        "title": "Get unsent edits",
        "description": (
            "List items with local edits not yet sent to their live marketplaces: which live "
            "marketplaces are behind, and for each changed field the value live now and the new "
            "local value (changes is null when Anteroom does not know which version a marketplace "
            "has). Sending these edits happens only in Anteroom."
        ),
        "annotations": _READ,
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {**_ID, "description": "Only this item."},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_PENDING_ITEMS, "default": 25},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "discard_pending_edits",
        "title": "Discard unsent edits",
        "description": (
            "Put an item back to the version last sent to its live marketplaces, dropping local "
            "edits that were never sent. Never contacts a marketplace. Pass the item's current "
            "revision_id (from get_listing or get_pending_edits). A scheduled draft is unscheduled "
            "like after any edit. When marketplaces were sent different versions, the newest is "
            "used unless marketplace names the one to match."
        ),
        "annotations": _DESTRUCTIVE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": _ID,
                "expected_revision_id": {**_ID, "description": "The item's current revision ID."},
                "marketplace": {"type": "string", "enum": sorted(MARKETS)},
            },
            "required": ["conversation_id", "expected_revision_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_labels",
        "title": "Get labels",
        "description": "List every label in the seller's inventory with the number of items wearing it.",
        "annotations": _READ,
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "rename_label",
        "title": "Rename label",
        "description": (
            "Rename a label on every item that wears it, in one step (case-insensitive). An item "
            "that already has the new label keeps one. To add a label to items, use "
            "manage_labels_and_notes. Labels are private and never sent to marketplaces."
        ),
        "annotations": {**_WRITE, "idempotentHint": True},
        "inputSchema": {
            "type": "object",
            "properties": {"label": _LABEL, "new_label": _LABEL},
            "required": ["label", "new_label"],
            "additionalProperties": False,
        },
    },
    {
        "name": "delete_label",
        "title": "Delete label",
        "description": (
            "Remove a label from every item that wears it, in one step (case-insensitive). The "
            "items are untouched otherwise. Requires confirm=true."
        ),
        "annotations": _DESTRUCTIVE,
        "inputSchema": {
            "type": "object",
            "properties": {"label": _LABEL, "confirm": {"type": "boolean"}},
            "required": ["label", "confirm"],
            "additionalProperties": False,
        },
    },
    {
        "name": "mark_not_duplicate",
        "title": "Mark items as not duplicates",
        "description": (
            "Record that two items are different, so the pair never shows as a potential "
            "duplicate again, the same as resolve_attention_items on a duplicate alert. Pass the "
            "duplicate alert's item_key from get_needs_attention, or first_id and second_id."
        ),
        "annotations": {**_WRITE, "idempotentHint": True},
        "inputSchema": {
            "type": "object",
            "properties": {"item_key": _ID, "first_id": _ID, "second_id": _ID},
            "additionalProperties": False,
        },
    },
]
# The permission area and level each tool needs (``services/mcp_tokens.py``).
PERMISSIONS: dict[str, tuple[str, str]] = {
    "get_pending_edits": ("listings", "read"),
    "discard_pending_edits": ("listings", "write"),
    "get_labels": ("listings", "read"),
    "rename_label": ("listings", "write"),
    "delete_label": ("listings", "write"),
    "mark_not_duplicate": ("attention", "write"),
}


def _text(args: dict[str, Any], key: str, *, required: bool = True) -> str:
    value = args.get(key)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200:
        raise ValueError(f"{key} is required: a non-empty string of at most 200 characters.")
    return value.strip()


def _count(args: dict[str, Any], key: str, *, default: int, maximum: int) -> int:
    value = args.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{key} must be a non-negative integer.")
    return min(value, maximum)


def _get_pending_edits(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    conv_id = _text(args, "conversation_id", required=False) or None
    limit = max(1, _count(args, "limit", default=25, maximum=MAX_PENDING_ITEMS))
    offset = _count(args, "offset", default=0, maximum=100_000)
    items = pending_edits.pending_edits(db, conv_id)
    return {
        "total": len(items),
        "offset": offset,
        "limit": limit,
        "items": items[offset:offset + limit],
        "note": "Local edits only. Nothing here was sent; the seller sends edits from Anteroom.",
    }


def _discard_pending_edits(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    conv_id = _text(args, "conversation_id")
    expected = _text(args, "expected_revision_id")
    marketplace = args.get("marketplace")
    if marketplace is not None and marketplace not in MARKETS:
        raise ValueError(f"marketplace must be one of: {', '.join(sorted(MARKETS))}.")
    result = pending_edits.discard_pending_edits(db, conv_id, expected_revision_id=expected, marketplace=marketplace)
    result["listing"] = stored_listing_fields(result["listing"])
    still = pending_edits.pending_edits(db, conv_id)
    return {
        "ok": True,
        "conversation_id": conv_id,
        **result,
        "marketplaces_still_behind": [row["marketplace"] for item in still for row in item["marketplaces"]],
        "pushed_to_marketplaces": False,
        "note": (
            "The local item now matches what its marketplaces show. No marketplace was contacted."
            if result["discarded"] else "Nothing to discard: the item already matches what was sent."
        ),
    }


def _get_labels(db: Session, _args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    labels = inventory_labels.label_counts(db)
    return {"total": len(labels), "labels": labels}


def _rename_label(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    return {"ok": True, **inventory_labels.rename_label(db, args.get("label"), args.get("new_label")),
            "pushed_to_marketplaces": False}


def _delete_label(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    if args.get("confirm") is not True:
        raise ValueError("Set confirm=true to remove this label from every item that wears it.")
    return {"ok": True, **inventory_labels.delete_label(db, args.get("label")), "pushed_to_marketplaces": False}


def _mark_not_duplicate(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    """The same decision as Resolve → Not duplicates on Home: listed under Resolved for good."""
    key = args.get("item_key")
    if key is not None:
        prefix, _, rest = _text(args, "item_key").partition(":")
        first, _, second = rest.partition(":")
        if prefix != "duplicate" or not first or not second:
            raise ValueError("item_key must be a potential duplicate's key from get_needs_attention (duplicate:…).")
    else:
        first, second = _text(args, "first_id"), _text(args, "second_id")
    first, second = sorted((first, second))
    if first == second:
        raise ValueError("first_id and second_id must name two different items.")
    try:
        home_dashboard.resolve_attention_item(db, item_key=f"duplicate:{first}:{second}", kind="duplicate_listing",
                                              fingerprint="")
    except similar_items.MergeError as exc:
        raise ValueError(str(exc)) from exc
    return {"ok": True, "first_id": first, "second_id": second, "not_duplicate": True}


HANDLERS = {
    "get_pending_edits": _get_pending_edits,
    "discard_pending_edits": _discard_pending_edits,
    "get_labels": _get_labels,
    "rename_label": _rename_label,
    "delete_label": _delete_label,
    "mark_not_duplicate": _mark_not_duplicate,
}
