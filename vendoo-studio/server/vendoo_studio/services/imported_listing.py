"""What an item's imported history says about it, as the inventory lists send it.

The desktop and hosted lists both read these fields: where the item was
imported as live, each listing's link, the dates per marketplace, and the
marketplaces Anteroom removed it from since.
"""
from __future__ import annotations

from vendoo_studio.services.marketplace_delist import removed_marketplaces
from vendoo_studio.services.vendoo_import import split_vendoo_labels


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
        "vendoo_sold_at": text("sold"),
        "vendoo_listed_dates": mapping("listedByMarketplace"),
        "vendoo_sold_dates": mapping("soldByMarketplace"),
    }


def imported_fields(notes: dict, direct_listing_urls: dict[str, str]) -> dict:
    """The row fields read from parsed notes. ``direct_listing_urls`` are Anteroom's own live links."""
    marketplaces = notes.get("vendooMarketplaces")
    return {
        "vendoo_status": str(notes.get("vendooStatus") or "") or None,
        "vendoo_cover_url": str(notes.get("vendooCoverUrl") or "") or None,
        "vendoo_labels": split_vendoo_labels(notes.get("vendooLabels")),
        "vendoo_marketplaces": [str(m) for m in marketplaces] if isinstance(marketplaces, list) else [],
        "removed_marketplaces": removed_marketplaces(notes, direct_listing_urls),
        "vendoo_listing_urls": _listing_urls(notes),
        **_dates(notes),
    }
