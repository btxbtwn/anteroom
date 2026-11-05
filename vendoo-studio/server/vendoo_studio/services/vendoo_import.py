"""Notes that items imported from Vendoo carry, read as stored.

Items the seller imported from Vendoo keep their history in the
conversation's notes: ``vendooItemId``, ``vendooStatus``, ``vendooLabels``
(the seller's labels), ``vendooMarketplaces`` and ``vendooDates``. Anteroom no
longer talks to Vendoo; these helpers only read and update those notes.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any


VENDOO_FIRST_DATE = datetime(2000, 1, 1, tzinfo=UTC)

_DATE_KEYS = ("created", "modified", "listed", "sold")
_DATE_BY_MARKETPLACE_KEYS = ("listedByMarketplace", "soldByMarketplace")


def parse_notes(raw: str | None) -> dict[str, Any]:
    """The conversation's notes, with Vendoo's zero dates left out.

    A stored zero date is not inert: read back it says the listing sold on
    1 January 1970, so Poshmark shows "Sold here", the item is refused a new
    listing, and its relist warning never fires. Every reader and every write
    of the notes goes through here, so the zeroes are dropped once instead of
    guarded at each call site.
    """
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return _without_zero_dates(data) if isinstance(data, dict) else {}


def _is_zero_date(value: Any) -> bool:
    """True for a written date that is Vendoo's zero rather than a date.

    An empty stamp is left alone: it is already "no date", and dropping the key
    would make these notes compare unequal to a freshly read set of dates.
    """
    return bool(str(value or "").strip()) and not _as_iso(value)


def _without_zero_dates(notes: dict[str, Any]) -> dict[str, Any]:
    """``notes`` with every Vendoo zero date removed."""
    dates = notes.get("vendooDates")
    if isinstance(dates, dict):
        kept = dict(dates)
        for key in _DATE_KEYS:
            if key in kept and _is_zero_date(kept[key]):
                kept.pop(key)
        for key in _DATE_BY_MARKETPLACE_KEYS:
            by_marketplace = kept.get(key)
            if isinstance(by_marketplace, dict):
                kept[key] = {name: when for name, when in by_marketplace.items() if not _is_zero_date(when)}
        notes["vendooDates"] = kept
    sale = notes.get("vendooSale")
    if isinstance(sale, dict) and _is_zero_date(sale.get("soldAt")):
        kept_sale = dict(sale)
        kept_sale.pop("soldAt")
        notes["vendooSale"] = kept_sale
    return notes


def split_vendoo_labels(raw: str | None) -> list[str]:
    """Vendoo labels are stored on the conversation as one comma-separated string."""
    return [label.strip() for label in str(raw or "").split(",") if label.strip()]


def vendoo_binding(notes: str | None) -> dict[str, str]:
    data = parse_notes(notes)
    item_id = str(data.get("vendooItemId") or "").strip()
    url = str(data.get("vendooUrl") or "").strip()
    out: dict[str, str] = {}
    if item_id:
        out["vendooItemId"] = item_id
    if url:
        out["vendooUrl"] = url
    elif item_id:
        out["vendooUrl"] = f"https://web.vendoo.co/app/item/{item_id}"
    return out


def merge_notes(existing: str | None, updates: dict[str, Any]) -> str:
    data = parse_notes(existing)
    binding_keys = {"vendooItemId", "vendooUrl"}
    for key, value in updates.items():
        if value is None:
            continue
        if key in binding_keys and not str(value).strip() and data.get(key):
            continue
        data[key] = value
    return json.dumps(data)


# Vendoo keys Vestiaire's API integration separately; Studio treats it as one marketplace.
VENDOO_MARKETPLACE_ALIASES = {"vestiaireApi": "vestiaire"}


def _as_iso(raw: Any) -> str:
    """One Vendoo date as an ISO 8601 string, whatever shape it arrived in.

    ``/api/item`` answers Firestore timestamps as ``{_seconds, _nanoseconds}``,
    a document read straight from Firestore carries an ISO string, and a few
    fields arrive as epoch numbers. Vendoo's zero date comes back as "", so no
    caller can mistake a date that never happened for one that did.
    """
    if isinstance(raw, dict):
        seconds = raw.get("_seconds", raw.get("seconds"))
        if seconds is None:
            return ""
        raw = float(seconds)
    if isinstance(raw, (int, float)):
        # Vendoo writes seconds in timestamps and milliseconds in plain numbers;
        # anything past the year 3000 read as seconds is really milliseconds.
        seconds = float(raw) / 1000 if float(raw) > 32503680000 else float(raw)
        try:
            parsed = datetime.fromtimestamp(seconds, UTC)
        except (OverflowError, OSError, ValueError):
            return ""
    else:
        text = str(raw or "").strip()
        if not text:
            return ""
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return ""
        if not parsed.tzinfo:
            parsed = parsed.replace(tzinfo=UTC)
    return parsed.isoformat() if parsed > VENDOO_FIRST_DATE else ""


def oldest_live_listed_at(notes: dict[str, Any]) -> str:
    """When the longest-standing live listing went up, as an ISO string.

    An item is only as fresh as its stalest live listing: a new
    listing says nothing about the copy that has sat on eBay for 90 days. A
    sold marketplace is finished and a delisted one has no copy to age, so
    neither counts. An item with no per-marketplace dates falls back to the
    item-wide listing date.
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
    live = notes.get("vendooMarketplaces")
    if not isinstance(live, list):
        live = []
    stamps = [
        _as_iso(listed_by[market])
        for market in live
        if listed_by.get(market) and not sold_by.get(market)
    ]
    stamps = [stamp for stamp in stamps if stamp]
    if stamps:
        return min(stamps, key=datetime.fromisoformat)
    return str(dates.get("listed") or "")
