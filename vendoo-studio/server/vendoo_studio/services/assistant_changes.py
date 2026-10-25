"""Changes the business assistant proposes, for the seller to apply or dismiss.

The assistant ends a reply with one fenced ``changes`` block: a JSON array
naming listings and the values they should take. Studio never acts on that
block by itself. It is checked against the listings as they are now, kept with
the reply as a proposal showing each value before and after, and written only
when the seller presses Apply.

A proposal can set a listing's asking price, cost, title, description, brand,
size, SKU, labels and box. What the seller reviews is what saving would really
do: a new size reaches every marketplace form and the title, so each of those
is shown. It cannot send, list, delist or publish anything.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from sqlalchemy.orm import Session

from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.listing import Listing, ListingRevision
from vendoo_studio.models.sourcing import SourceBox

CHANGES_FENCE = "```changes"
# Listings one proposal may touch.
MAX_CHANGES = 300
# What a proposal may set, by the names the brief's listing rows use.
TEXT_FIELDS = {"title": 200, "description": 10_000, "brand": 120, "size": 40, "sku": 120}
FIELDS = ("asking", "cost", *TEXT_FIELDS, "labels", "box")
# What of a listing the seller reviews, in the order shown. Each marketplace's size follows `size`.
SHOWN = ("asking", "cost", "title", "description", "brand", "size", "sku", "labels")
MAX_LABELS = 30
# How much of an earlier proposal is repeated to the model with the conversation.
HISTORY_CHARS = 4000


def visible_text(reply: str) -> str:
    """What of a reply still being written the seller may read: nothing of a changes block.

    A tail that could be the start of the block's fence is held until the next piece settles it.
    """
    lowered = reply.lower()
    at = lowered.find(CHANGES_FENCE)
    if at >= 0:
        return reply[:at]
    for size in range(min(len(CHANGES_FENCE) - 1, len(reply)), 0, -1):
        if CHANGES_FENCE.startswith(lowered[-size:]):
            return reply[:-size]
    return reply


def split_reply(reply: str) -> tuple[str, str | None]:
    """A finished reply as the words for the seller and the changes block, when it has one."""
    at = reply.lower().find(CHANGES_FENCE)
    if at < 0:
        return reply, None
    block = reply[at + len(CHANGES_FENCE):]
    end = block.find("```")
    return reply[:at], (block if end < 0 else block[:end]).strip()


def propose(db: Session, block: str) -> dict[str, Any] | None:
    """The proposal a changes block asks for, or None when nothing in it can be applied.

    Entries naming a listing Studio does not hold, fields it may not set, and
    values the listing already has are left out.
    """
    try:
        entries = json.loads(block)
    except ValueError:
        return None
    if not isinstance(entries, list):
        return None
    wanted: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("set"), dict):
            continue
        listing_id = str(entry.get("listing") or "").strip()
        if listing_id:
            wanted.setdefault(listing_id, {}).update(entry["set"])
    if not wanted:
        return None

    conversations = {
        conv.id: conv for conv in db.query(Conversation).filter(Conversation.id.in_(wanted)).all()
    }
    listings = dict(
        db.query(Listing.conversation_id, ListingRevision.listing_json)
        .join(ListingRevision, ListingRevision.id == Listing.current_revision_id)
        .filter(Listing.conversation_id.in_(wanted))
        .all()
    )
    boxes = {box.id: box for box in db.query(SourceBox).all()}
    items = []
    for listing_id, values in wanted.items():
        conv = conversations.get(listing_id)
        if conv is None:
            continue
        listing = listings.get(listing_id)
        to_set: dict[str, Any] = {}
        fields: list[dict[str, Any]] = []
        if isinstance(listing, dict):
            to_set = {
                field: value
                for field in FIELDS if field in values and field != "box"
                for value in [_wanted(field, values[field])] if value is not None
            }
            fields = _differences(listing, saved_as(listing, to_set)) if to_set else []
            if not fields:
                to_set = {}
        box_id = str(values.get("box")).strip() if values.get("box") else None
        if "box" in values and box_id != conv.box_id and (box_id is None or box_id in boxes):
            to_set["box"] = box_id
            # The seller reads a box by its name; the id is what gets assigned.
            fields.append({
                "field": "box", "old": _box_name(boxes.get(conv.box_id)), "new": _box_name(boxes.get(box_id)),
            })
        if fields:
            items.append({
                "listing_id": listing_id, "title": conv.title or "Untitled", "set": to_set, "fields": fields,
            })
        if len(items) == MAX_CHANGES:
            break
    return {"status": "proposed", "items": items} if items else None


def saved_as(listing: dict, to_set: dict[str, Any]) -> dict:
    """A copy of ``listing`` as it would be saved with a proposal's values: what the seller reviews.

    The listing editor's save cleans sizes, carries the general size to every
    marketplace form and the title, and rewrites dropdown values, so a proposal
    that sets one value can move several.
    """
    from vendoo_studio.models.validation import normalize_listing_dropdowns
    from vendoo_studio.services.size_audit import aligned

    previous = listing.get("size")
    updated = aligned(
        apply_fields(listing, to_set),
        previous_size=None if previous is None else str(previous),
    )
    normalize_listing_dropdowns(updated)
    return updated


def apply_fields(listing: dict, to_set: dict[str, Any]) -> dict:
    """A copy of ``listing`` with a proposal's listing values set. The box is not part of a listing."""
    from vendoo_studio.services.price_drop import apply_price_to_listing

    updated = copy.deepcopy(listing)
    for field, value in to_set.items():
        if field == "asking":
            updated = apply_price_to_listing(updated, value)
        elif field != "box":
            updated[field] = value
    return updated


def history_note(changes: dict[str, Any] | None) -> str:
    """What the model is told about a proposal it made earlier, and what became of it."""
    if not changes:
        return ""
    items = changes.get("items") or []
    skipped = sum(1 for item in items if item.get("skipped"))
    state = {
        "proposed": "The seller has not applied or dismissed them yet, so nothing has changed.",
        "dismissed": "The seller dismissed them, so nothing changed.",
        "applied": f"The seller applied them; {len(items) - skipped} listings changed and {skipped} were skipped.",
    }.get(str(changes.get("status")), "")
    proposed = json.dumps(
        [
            {"listing": item["listing_id"], "set": item["set"]}
            for item in items
        ],
        separators=(",", ":"), default=str,
    )
    if len(proposed) > HISTORY_CHARS:
        proposed = f"{proposed[:HISTORY_CHARS]}… (cut short)"
    return f"\n\n(Anteroom note: this reply proposed changes to {len(items)} listings. {state} Proposed: {proposed})"


def _differences(before: dict, after: dict) -> list[dict[str, Any]]:
    """Each value the seller reviews that differs between a listing and how it would be saved."""
    from vendoo_studio.services.size_audit import form_sizes

    changes = [
        {"field": field, "old": old, "new": new}
        for field in SHOWN
        for old, new in [(_current(field, before), _current(field, after))] if old != new
    ]
    sizes, saved = form_sizes(before), form_sizes(after)
    at = next((index + 1 for index, change in enumerate(changes) if change["field"] == "size"), len(changes))
    changes[at:at] = [
        {"field": f"{name}_size", "old": sizes.get(name), "new": size}
        for name, size in saved.items() if sizes.get(name) != size
    ]
    return changes


def _wanted(field: str, raw: Any) -> Any:
    from vendoo_studio.services.price_drop import whole_dollars

    if field in ("asking", "cost"):
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < 0 or raw > 1_000_000:
            return None
        if field == "asking":
            return whole_dollars(raw) if raw > 0 else None
        return round(float(raw), 2)
    if field == "labels":
        if isinstance(raw, str):
            raw = raw.split(",")
        if not isinstance(raw, list):
            return None
        labels = list(dict.fromkeys(str(label).strip() for label in raw if str(label).strip()))
        return labels[:MAX_LABELS]
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if len(text) > TEXT_FIELDS[field] or (not text and field in ("title", "size")):
        return None
    return text


def _current(field: str, listing: dict) -> Any:
    from vendoo_studio.services.price_drop import listing_price

    if field == "asking":
        price = listing_price(listing)
        return int(price) if price is not None and price == int(price) else price
    if field == "cost":
        cost = listing.get("cost")
        return round(float(cost), 2) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None
    if field == "labels":
        labels = listing.get("labels")
        return [str(label) for label in labels] if isinstance(labels, list) else []
    return str(listing.get(field) or "").strip()


def _box_name(box: SourceBox | None) -> str | None:
    if box is None:
        return None
    return " · ".join(part for part in (box.store, box.title) if part) or "Untitled box"
