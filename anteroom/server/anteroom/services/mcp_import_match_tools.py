"""MCP tools for the import look-alike review: the assistant gets the same review the seller has.

Import all holds a marketplace listing that looks like an item already in
Anteroom instead of importing a duplicate (``services/import_reviews.py``).
These tools list those holds, link one to its item exactly like the import
screen's **Link to this item**, or record that the two are different products.

Nothing here lists, updates or delists anything on a marketplace. Confirming a
match only reads the marketplace, to check the listing is still the seller's
and still for sale, and that read runs after the call's database session is
closed (``finish_pending_links``), within the call's deadline.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from sqlalchemy.orm import Session

from anteroom.services import import_reviews

_LINK_PLACEHOLDER = "_anteroom_import_link"
_NAMES = {"ebay": "eBay", "etsy": "Etsy", "poshmark": "Poshmark", "mercari": "Mercari", "depop": "Depop"}

_READ = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
_WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
_REVIEW_ID = {"type": "string", "description": "The review ID from get_import_matches."}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_import_matches",
        "title": "List import look-alikes",
        "description": (
            "List marketplace listings an import left for review because they look like an item already "
            "in Anteroom. Each has the listing (marketplace, title, price, link), the suggested existing "
            "item (conversation_id, title, price, photo count) and why it matched. Compare them, e.g. with "
            "get_listing and get_listing_photos, then call confirm_import_match or reject_import_match."
        ),
        "annotations": _READ,
        "inputSchema": {
            "type": "object",
            "properties": {
                "marketplace": {"type": "string", "enum": list(import_reviews.MARKETPLACES),
                                "description": "Only listings from this marketplace."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "confirm_import_match",
        "title": "Confirm import look-alike",
        "description": (
            "The listing and the suggested item are the same product: link the marketplace listing to that "
            "existing Anteroom item, exactly like Link to this item in Anteroom. The item's edits and photos "
            "are kept. Anteroom first reads the marketplace to check the listing is still the seller's and "
            "still for sale; nothing is listed, changed or delisted on any marketplace."
        ),
        "annotations": _WRITE,
        "inputSchema": {
            "type": "object",
            "properties": {"review_id": _REVIEW_ID},
            "required": ["review_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "reject_import_match",
        "title": "Reject import look-alike",
        "description": (
            "The listing and the suggested item are different products. Anteroom stops suggesting that item "
            "for the listing, and the seller's next Import all brings the listing in as its own item (or they "
            "import it from Import from <marketplace>). Only changes Anteroom."
        ),
        "annotations": _WRITE,
        "inputSchema": {
            "type": "object",
            "properties": {"review_id": _REVIEW_ID},
            "required": ["review_id"],
            "additionalProperties": False,
        },
    },
]

PERMISSIONS: dict[str, tuple[str, str]] = {
    "get_import_matches": ("listings", "read"),
    "confirm_import_match": ("listings", "write"),
    "reject_import_match": ("listings", "write"),
}


def _review_id(args: dict[str, Any]) -> str:
    value = args.get("review_id")
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 64:
        raise ValueError("review_id is required: use an ID from get_import_matches.")
    return value.strip()


def _get_import_matches(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    marketplace = args.get("marketplace")
    if marketplace is not None and marketplace not in import_reviews.MARKETPLACES:
        raise ValueError(f"marketplace must be one of: {', '.join(import_reviews.MARKETPLACES)}.")
    limit = args.get("limit", 50)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be a whole number from 1 to 100.")
    reviews = import_reviews.pending(db, marketplace, limit)
    return {"reviews": reviews, "count": len(reviews), "pushed_to_marketplaces": False}


def _confirm_import_match(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    try:
        review = import_reviews.get_pending(db, _review_id(args))
    except import_reviews.ReviewNotFound as exc:
        raise ValueError(str(exc)) from exc
    # The marketplace check and the link run once this session is closed.
    link = {"review_id": review.id, "marketplace": review.marketplace, "listing_id": review.listing_id,
            "conversation_id": review.conversation_id}
    return {**link, "pushed_to_marketplaces": False,
            "_after": [{"type": "text", "text": "", _LINK_PLACEHOLDER: link}]}


def _reject_import_match(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    try:
        return {**import_reviews.reject(db, _review_id(args)), "pushed_to_marketplaces": False}
    except import_reviews.ReviewNotFound as exc:
        raise ValueError(str(exc)) from exc


HANDLERS = {
    "get_import_matches": _get_import_matches,
    "confirm_import_match": _confirm_import_match,
    "reject_import_match": _reject_import_match,
}


def pending_links(body: Any) -> list[dict[str, Any]]:
    """The tool results in a response that still have to link a listing."""
    found = []
    for response in body if isinstance(body, list) else [body]:
        result = response.get("result") if isinstance(response, dict) else None
        content = result.get("content") if isinstance(result, dict) else None
        if isinstance(content, list) and any(isinstance(item, dict) and _LINK_PLACEHOLDER in item for item in content):
            found.append(result)
    return found


async def _link(marketplace: str, listing_id: str, conversation_id: str) -> dict:
    """The import screen's Link to this item, for one marketplace."""
    if marketplace == "ebay":
        from anteroom.services import ebay_import
        from anteroom.services.ebay_client import EbayClient

        return await ebay_import.link_listing(await EbayClient.connect(), listing_id, conversation_id)
    if marketplace == "etsy":
        from anteroom.services import etsy_import
        from anteroom.services.etsy_workspace import EtsyClient

        return await etsy_import.link_listing(await EtsyClient.connect(), listing_id, conversation_id)
    from anteroom.services import chrome_import

    return await chrome_import.link_listing(marketplace, listing_id, conversation_id)


def _failure(marketplace: str, exc: BaseException) -> str:
    import httpx

    from anteroom.services.chrome_import import ChromeImportError
    from anteroom.services.ebay_client import EbayFailure
    from anteroom.services.etsy_workspace import EtsyFailure
    from anteroom.services.marketplace_accounts import SignInRequired

    name = _NAMES.get(marketplace, marketplace)
    if isinstance(exc, TimeoutError):
        return f"{name} did not answer in time, so nothing was linked. Try again."
    if isinstance(exc, (EbayFailure, EtsyFailure, ChromeImportError)):
        return f"Nothing was linked: {exc}"
    if isinstance(exc, SignInRequired):
        return f"Nothing was linked: connect {name} again in Anteroom Settings → Connections."
    if isinstance(exc, httpx.HTTPError):
        return f"Nothing was linked: Anteroom could not reach {name}. Try again."
    return "Nothing was linked: this listing could not be checked. Link it from Anteroom's import screen."


async def finish_pending_links(results: list[dict[str, Any]], *, deadline: float) -> None:
    """Check each listing on its marketplace and link it, replacing the tool result with the outcome.

    Runs after the call's database session is closed. ``deadline`` is a
    ``time.monotonic()`` value; a check still running then is abandoned before
    anything is written.
    """
    for result in results:
        link = next(item[_LINK_PLACEHOLDER] for item in result["content"]
                    if isinstance(item, dict) and _LINK_PLACEHOLDER in item)
        marketplace = link["marketplace"]
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            outcome = await asyncio.wait_for(
                _link(marketplace, link["listing_id"], link["conversation_id"]), timeout=remaining)
        except Exception as exc:  # noqa: BLE001 - each failure is reported to the assistant by name
            result["content"] = [{"type": "text", "text": _failure(marketplace, exc)}]
            result["isError"] = True
            continue
        result["content"] = [{"type": "text", "text": json.dumps({
            **link, "linked": True, "photo_count": outcome.get("photo_count"),
            "warnings": outcome.get("warnings", []), "pushed_to_marketplaces": False,
        }, indent=2, default=str)}]
        result["isError"] = False
