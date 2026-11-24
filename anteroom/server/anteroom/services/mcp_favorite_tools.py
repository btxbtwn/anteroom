"""MCP tools for the seller's starred items, wired into ``services/mcp_http.py``.

Starring changes only Anteroom: it never reaches a marketplace, and it is not
an edit, so the items keep their last-updated time and gain no unsent edits.
Errors are ``ValueError``, which the MCP dispatcher returns as tool errors.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from anteroom.repositories.queries import ConversationRepo

MAX_FAVORITE_IDS = 100

TOOLS: list[dict[str, Any]] = [
    {
        "name": "set_favorite",
        "title": "Star or unstar items",
        "description": (
            "Star (favorite) or unstar one or many items in the seller's Anteroom inventory, "
            "so they show under the Favorites filter. Local to Anteroom only: it never "
            "changes a marketplace listing and does not count as an edit."
        ),
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_ids": {
                    "type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": MAX_FAVORITE_IDS,
                    "description": "Listing / conversation IDs to change.",
                },
                "favorite": {"type": "boolean", "description": "true stars the items, false unstars them."},
            },
            "required": ["conversation_ids", "favorite"],
            "additionalProperties": False,
        },
    },
]

PERMISSIONS: dict[str, tuple[str, str]] = {"set_favorite": ("listings", "write")}


def _set_favorite(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    ids = args.get("conversation_ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(value, str) and value.strip() for value in ids):
        raise ValueError("conversation_ids must be a non-empty list of listing IDs.")
    if len(ids) > MAX_FAVORITE_IDS:
        raise ValueError(f"conversation_ids takes at most {MAX_FAVORITE_IDS} IDs per call.")
    favorite = args.get("favorite")
    if not isinstance(favorite, bool):
        raise ValueError("favorite must be true or false.")
    wanted = list(dict.fromkeys(value.strip() for value in ids))
    updated = ConversationRepo(db).set_favorites(wanted, favorite)
    if not updated:
        raise ValueError("No items found for those conversation_ids.")
    found = set(updated)
    return {
        "favorite": favorite,
        "updated": updated,
        "not_found": [value for value in wanted if value not in found],
        "note": "Starred in Anteroom only; this never changes a marketplace listing.",
    }


HANDLERS = {"set_favorite": _set_favorite}
