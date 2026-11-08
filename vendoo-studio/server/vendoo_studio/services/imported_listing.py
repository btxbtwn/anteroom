"""What an item's imported history says about it, as the inventory lists send it.

The desktop and hosted lists both read these fields: where the item was
imported as live, each listing's link, the dates per marketplace, and the
marketplaces Anteroom removed it from since, plus how long the item has
been listed, which also counts the listings Anteroom put up itself.
"""
from __future__ import annotations

from datetime import datetime

from vendoo_studio.models.marketplace_listing import LIVE_STATES
from vendoo_studio.services.marketplace_delist import removed_marketplaces
from vendoo_studio.services.vendoo_import import as_iso, split_vendoo_labels


def _listing_urls(notes: dict) -> dict[str, str]:
    raw = notes.get("vendooListingUrls")
    if not isinstance(raw, dict):
        return {}
    return {str(market): str(url) for market, url in raw.items() if url}


def _dates(notes: dict) -> dict:
    """The imported time tracking for the row, as the sidebar and details read it."""
    dates = notes.get("vendooDates")
    if not isinstance(dates, dict):
        dates = {}

    def text(key: str) -> str | None:
        return str(dates.get(key) or "") or None

    def mapping(key: str) -> dict[str, str]:
        raw = dates.get(key)
        if not isinstance(raw, dict):
            return {}
        return {str(market): str(stamp) for market, stamp in raw.items() if stamp}

    return {
        "vendoo_created_at": text("created"),
        "vendoo_modified_at": text("modified"),
        "vendoo_listed_at": text("listed"),
        "vendoo_listed_dates": mapping("listedByMarketplace"),
        "vendoo_sold_dates": mapping("soldByMarketplace"),
    }


def _live_urls(direct: dict[str, dict]) -> dict[str, str]:
    return {market: str(record["listing_url"]) for market, record in direct.items() if record.get("listing_url")}


def oldest_live_listed_at(notes: dict, direct: dict[str, dict]) -> str:
    """When the item's longest-standing live listing went up, as an ISO string.

    An item is only as fresh as its stalest live listing: a new listing on one
    marketplace says nothing about the copy that has sat on another for 90
    days. Each marketplace counts once. A listing Anteroom put up replaces the
    imported date there, so a relist starts that marketplace over. A sold
    marketplace is finished and a removed one has no copy to age, so neither
    counts. With no per-marketplace dates, the item-wide listing date is used.

    ``direct`` is Anteroom's own listing records by marketplace, as
    ``MarketplaceListingRepo.summaries`` returns them.
    """
    dates = notes.get("vendooDates")
    if not isinstance(dates, dict):
        dates = {}
    listed_by = dates.get("listedByMarketplace")
    if not isinstance(listed_by, dict):
        listed_by = {}
    sold_by = dates.get("soldByMarketplace")
    if not isinstance(sold_by, dict):
        sold_by = {}
    imported = notes.get("vendooMarketplaces")
    if not isinstance(imported, list):
        imported = []
    removed = set(removed_marketplaces(notes, _live_urls(direct)))
    stamps: dict[str, str] = {
        str(market): as_iso(listed_by[market])
        for market in imported
        if listed_by.get(market) and not sold_by.get(market) and market not in removed
    }
    for market, record in direct.items():
        if record.get("state") in LIVE_STATES and record.get("listed_at"):
            stamps[market] = as_iso(record["listed_at"])
    known = [stamp for stamp in stamps.values() if stamp]
    if known:
        return min(known, key=datetime.fromisoformat)
    return str(dates.get("listed") or "")


def imported_fields(notes: dict, direct: dict[str, dict]) -> dict:
    """The row fields read from parsed notes and Anteroom's own listing records by marketplace."""
    marketplaces = notes.get("vendooMarketplaces")
    return {
        "vendoo_status": str(notes.get("vendooStatus") or "") or None,
        "vendoo_cover_url": str(notes.get("vendooCoverUrl") or "") or None,
        "vendoo_labels": split_vendoo_labels(notes.get("vendooLabels")),
        "vendoo_marketplaces": [str(m) for m in marketplaces] if isinstance(marketplaces, list) else [],
        "removed_marketplaces": removed_marketplaces(notes, _live_urls(direct)),
        "vendoo_listing_urls": _listing_urls(notes),
        **_dates(notes),
        "oldest_listed_at": oldest_live_listed_at(notes, direct) or None,
    }
