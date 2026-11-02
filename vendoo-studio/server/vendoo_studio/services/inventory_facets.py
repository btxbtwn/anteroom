"""Display facets derived from the inventory's scalar database projections."""
from __future__ import annotations

import json
from typing import Any
from sqlalchemy.orm import Session
from vendoo_studio.repositories.queries import ConversationRepo

def _facet(sku: Any, price: Any, etsy: Any = None, year: Any = None) -> dict[str, Any]:
    from vendoo_studio.services.etsy_fields import etsy_eligible

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
        "ebay_specifics": {"yearManufactured": year} if year else {},
    }
    return {"sku": text or None, "price": amount, "etsy_eligible": etsy_eligible(listing)}


def listing_facets(db: Session) -> dict[str, dict[str, Any]]:
    return {conv_id: _facet(*values) for conv_id, *values in ConversationRepo(db).listing_facet_rows()}


def listing_facet(db: Session, conv_id: str) -> dict[str, Any]:
    row = ConversationRepo(db).listing_facet_row(conv_id)
    return _facet(*row) if row else {"sku": None, "price": None, "etsy_eligible": True}
