"""Display facets derived from the inventory's database projections."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from sqlalchemy.orm import Session
from anteroom.repositories.queries import ConversationRepo

def scheduled_publish_iso(when: datetime | None) -> str | None:
    # The schedule stores UTC without a timezone in the database.
    return when.isoformat() + "Z" if when is not None else None


def _facet(sku: Any, price: Any, etsy: Any, ebay: Any, scheduled_publish_at: datetime | None) -> dict[str, Any]:
    from anteroom.services.etsy_fields import etsy_eligible

    text = str(sku or "").strip()
    try:
        amount = float(price) if price is not None and str(price) != "" else None
    except (TypeError, ValueError):
        amount = None
    # json_value hands back a dict; JSON text is still accepted.
    try:
        specifics = json.loads(etsy) if isinstance(etsy, str) else etsy
    except json.JSONDecodeError:
        specifics = None
    listing = {
        "etsy_specifics": specifics if isinstance(specifics, dict) else {},
        "ebay_specifics": ebay,
    }
    return {
        "sku": text or None,
        "price": amount,
        "etsy_eligible": etsy_eligible(listing),
        "scheduled_publish_at": scheduled_publish_iso(scheduled_publish_at),
    }


def listing_facets(db: Session) -> dict[str, dict[str, Any]]:
    return {conv_id: _facet(*values) for conv_id, *values in ConversationRepo(db).listing_facet_rows()}


def listing_facet(db: Session, conv_id: str) -> dict[str, Any]:
    row = ConversationRepo(db).listing_facet_row(conv_id)
    return _facet(*row) if row else {"sku": None, "price": None, "etsy_eligible": True}
