"""Items live on Etsy that Etsy does not allow, for the seller to delist.

Etsy removes modern resale under its Creativity Standards, and repeat removals
cost Star Seller and can suspend the shop. Send no longer fills Etsy for these
items, but ones listed before that are still up. This lists them and nothing
more: delisting stays with the seller (AGENTS.md — automation never publishes
or delists).

Read from what Studio last synced from Vendoo, so an item Etsy already took
down shows here until the next sync notices.
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from vendoo_studio.database import json_value
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.etsy_fields import (
    etsy_eligible,
    etsy_when_raw,
    etsy_when_options,
    etsy_who_raw,
    resolve_etsy_when,
)
from vendoo_studio.models.listing import Listing, ListingRevision
from vendoo_studio.services.vendoo_import import parse_notes

# A title that dates the piece. "Y2K" is left out: it names a look that new
# clothes are sold under too.
_VINTAGE_TITLE = re.compile(r"\b(?:vintage|19\d0s|[5-9]0s|19[0-9]{2})\b", re.IGNORECASE)


def etsy_not_allowed_live(db: Session) -> list[dict[str, Any]]:
    """Unsold Etsy listings whose item fails ``etsy_eligible``, oldest listing first."""
    doc = ListingRevision.listing_json
    rows = (
        db.query(
            Conversation.id,
            Conversation.title,
            Conversation.notes,
            json_value(doc, "title"),
            json_value(doc, "etsy_specifics"),
            func.coalesce(
                json_value(doc, "ebay_specifics.yearManufactured"),
                json_value(doc, "ebay_specifics.year_manufactured"),
            ),
        )
        .join(Listing, Listing.conversation_id == Conversation.id)
        .join(ListingRevision, ListingRevision.id == Listing.current_revision_id)
        .filter(Conversation.notes.like('%"etsy"%'))
        .all()
    )
    out: list[dict[str, Any]] = []
    for conv_id, conv_title, raw_notes, title, raw_etsy, year in rows:
        notes = parse_notes(raw_notes)
        live = notes.get("vendooMarketplaces")
        if not isinstance(live, list) or "etsy" not in live:
            continue
        dates = notes.get("vendooDates") if isinstance(notes.get("vendooDates"), dict) else {}
        sold = dates.get("soldByMarketplace") if isinstance(dates.get("soldByMarketplace"), dict) else {}
        if sold.get("etsy"):
            continue
        listing = {
            "etsy_specifics": _json_object(raw_etsy),
            "ebay_specifics": {"yearManufactured": year} if year else {},
        }
        if etsy_eligible(listing):
            continue
        etsy = listing["etsy_specifics"]
        when = etsy_when_raw(etsy, listing)
        name = str(title or conv_title or "").strip() or "Untitled"
        listed = dates.get("listedByMarketplace") if isinstance(dates.get("listedByMarketplace"), dict) else {}
        urls = notes.get("vendooListingUrls") if isinstance(notes.get("vendooListingUrls"), dict) else {}
        out.append({
            "conversation_id": conv_id,
            "title": name,
            "when_made": resolve_etsy_when(when, etsy_when_options()) if when else "",
            "who_made": etsy_who_raw(etsy),
            "listed_at": str(listed.get("etsy") or "") or None,
            "etsy_url": str(urls.get("etsy") or "") or None,
            # Listed as made to order or recent, yet the title dates it: the
            # answer may be wrong rather than the item, and fixing When Made
            # keeps it on Etsy.
            "title_says_vintage": bool(_VINTAGE_TITLE.search(name)),
        })
    out.sort(key=lambda row: (row["listed_at"] or "", row["title"]))
    return out


def _json_object(raw: Any) -> dict[str, Any]:
    """``json_value`` hands back a dict; older callers passed JSON text."""
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw) if isinstance(raw, str) else None
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
