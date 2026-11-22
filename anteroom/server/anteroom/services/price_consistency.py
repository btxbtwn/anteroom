"""Which inventory rows have marketplace prices that drifted from the seller's pricing rules.

The inventory list reads only the few values a check needs (the item price,
the seller's locks and each marketplace's main price key), extracted inside
the database, and only while pricing rules are on.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from anteroom.database import json_value
from anteroom.models.listing import Listing, ListingRevision
from anteroom.models.marketplace_listing import LIVE_STATES
from anteroom.services.pricing_rules import active_rules, price_drift
from anteroom.services.seller_overrides import CANONICAL_PRICE, SELLER_FIELDS, set_path


def _columns() -> tuple:
    doc = ListingRevision.listing_json
    return (json_value(doc, SELLER_FIELDS), json_value(doc, "price"),
            *(json_value(doc, path) for path in CANONICAL_PRICE.values()))


def _listing(locks: Any, price: Any, *prices: Any) -> dict:
    listing: dict = {"price": price}
    if isinstance(locks, list):
        listing[SELLER_FIELDS] = locks
    for path, value in zip(CANONICAL_PRICE.values(), prices, strict=True):
        if value is not None:
            set_path(listing, path, value)
    return listing


def price_listings(db: Session, conv_id: str | None = None) -> dict[str, dict]:
    """Each item's prices and locks, as a small listing ``price_drift`` reads."""
    query = select(Listing.conversation_id, *_columns()).join(
        ListingRevision, (ListingRevision.id == Listing.current_revision_id)
        & (ListingRevision.conversation_id == Listing.conversation_id))
    if conv_id is not None:
        query = query.where(Listing.conversation_id == conv_id)
    return {identity: _listing(*values) for identity, *values in db.execute(query)}


def live_prices(direct: dict[str, dict] | None) -> dict[str, Any]:
    return {market: record.get("price") for market, record in (direct or {}).items()
            if record.get("state") in LIVE_STATES and record.get("price") is not None}


class DriftReader:
    """Reads the rules once per request, and the price inputs only when they are on."""

    def __init__(self, db: Session, conv_id: str | None = None):
        self.rules = active_rules()
        self.listings = price_listings(db, conv_id) if self.rules else {}

    def drift(self, conv_id: str, direct: dict[str, dict] | None) -> list[dict]:
        listing = self.listings.get(conv_id)
        if not self.rules or listing is None:
            return []
        return price_drift(listing, self.rules, live_prices(direct))
