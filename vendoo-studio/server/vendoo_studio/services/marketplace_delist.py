"""Seller-confirmed delisting: delete an item's live listings on named marketplaces.

Delist means the listing is removed from the marketplace. eBay has no delete,
so its listing is ended; Sellwild gives sellers only its own delist. The
marketplace where the item sold is never touched. Nothing here runs on a
timer, a queue, or a retry: each call names its marketplaces and carries the
seller's confirmation. Deleting is safe to repeat, because every marketplace
step reads the listing first and reports one that is already gone as deleted.

The seller can also correct what Anteroom knows: mark a marketplace listed
(with its link), sold, or not listed. A mark changes only Anteroom's record,
never the marketplace, and stands until the marketplace reports something
newer: a sale after a "listed" mark, or a different listing after a "not
listed" one.
"""
from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
from sqlalchemy.orm import Session

from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.repositories.queries import ConversationRepo, JobRepo
from vendoo_studio.services import browser_bridge, marketplace_accounts
from vendoo_studio.services.api_job_lock import start_gate
from vendoo_studio.services.vendoo_import import merge_notes, parse_notes

MARKETPLACES = ("ebay", "etsy", "poshmark", "mercari", "depop", "sellwild")
BROWSER_MARKETPLACES = {"poshmark", "mercari", "depop", "sellwild"}

# The listing ID each marketplace's public link carries.
LISTING_ID = {
    "ebay": re.compile(r"^https://(?:www\.)?ebay\.com/itm/(?:[^/?#]+/)?(\d{9,15})(?:[/?#]|$)"),
    "etsy": re.compile(r"^https://www\.etsy\.com/(?:[a-z-]+/)?listing/([1-9]\d*)(?:[/?#]|$)"),
    "poshmark": re.compile(r"^https://poshmark\.com/(?:my-)?listing/(?:[^/?#]*-)?([a-f0-9]{24})(?:[/?#]|$)"),
    "mercari": re.compile(r"^https://www\.mercari\.com/us/item/(m\d{6,})/?(?:[?#]|$)"),
    "depop": re.compile(r"^https://www\.depop\.com/products/([a-z0-9][a-z0-9-]{0,200})/?(?:[?#]|$)"),
    "sellwild": re.compile(r"^https://sellwild\.com/product/(\d+)(?:/|$)"),
}

_running = asyncio.Lock()


class DelistError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code


def listing_id(marketplace: str, url: str | None) -> str | None:
    pattern = LISTING_ID.get(marketplace)
    match = pattern.match(str(url or "").strip()) if pattern else None
    return match.group(1) if match else None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _time(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _sold_on(db: Session, conv_id: str, notes: dict) -> dict[str, datetime | None]:
    """Where the item sold, and when that was last reported (None when unknown)."""
    sold: dict[str, datetime | None] = {}

    def add(marketplace: str, when) -> None:
        stamp, known = _time(when), sold.get(marketplace)
        sold[marketplace] = max(filter(None, (stamp, known)), default=None)

    for row in db.query(MarketplaceSale).filter(MarketplaceSale.conversation_id == conv_id):
        add(row.marketplace, row.received_at)
    sale = notes.get("vendooSale") if isinstance(notes.get("vendooSale"), dict) else {}
    if sale.get("marketplace"):
        add(str(sale["marketplace"]).lower(), None)
    dates = notes.get("vendooDates") if isinstance(notes.get("vendooDates"), dict) else {}
    sold_by = dates.get("soldByMarketplace") if isinstance(dates.get("soldByMarketplace"), dict) else {}
    for name, when in sold_by.items():
        if when:
            add(str(name).lower(), when)
    return sold


def _notes_dict(notes: dict, key: str) -> dict:
    value = notes.get(key)
    return value if isinstance(value, dict) else {}


def listings(db: Session, conv_id: str) -> dict:
    """Each marketplace listing Anteroom knows for this item, and what Delist would do."""
    conv = ConversationRepo(db).get(conv_id)
    if not conv:
        raise DelistError(404, "Conversation not found")
    notes = parse_notes(conv.notes)
    urls = _notes_dict(notes, "vendooListingUrls")
    native = _notes_dict(notes, "nativeMarketplaceDrafts")
    delists = _notes_dict(notes, "marketplaceDelists")
    marks = _notes_dict(notes, "marketplaceMarks")
    sold = _sold_on(db, conv_id, notes)
    result = {}
    for marketplace in MARKETPLACES:
        binding = native.get(marketplace) if isinstance(native.get(marketplace), dict) else {}
        mark = marks.get(marketplace) if isinstance(marks.get(marketplace), dict) else {}
        candidates = [(binding.get("listing_url"), "anteroom"), (urls.get(marketplace), "vendoo")]
        if mark.get("state") == "listed":
            candidates.insert(0, (mark.get("url"), "seller"))
        url, source = next(((u, s) for u, s in candidates if listing_id(marketplace, u)), (None, None))
        item_id = listing_id(marketplace, url)
        if mark.get("state") == "not_listed" and (not item_id or item_id == mark.get("listing_id")):
            continue
        sold_here = marketplace in sold
        if mark.get("state") == "listed":
            reported, marked = sold.get(marketplace), _time(mark.get("at"))
            sold_here = bool(reported and marked and reported > marked)
        elif mark.get("state") == "sold":
            sold_here = True
        record = delists.get(marketplace) if isinstance(delists.get(marketplace), dict) else {}
        if not url and not record and not sold_here:
            continue
        item_id = item_id or record.get("listing_id")
        # A newer listing than the one deleted is live again.
        if record and item_id and record.get("listing_id") != item_id:
            record = {}
        result[marketplace] = {"url": url or record.get("url"), "listing_id": item_id, "source": source,
                               "sold_here": sold_here, "state": record.get("state") or "live",
                               "error": record.get("error"), "at": record.get("at")}
    return result


def _store(db: Session, conv_id: str, key: str, marketplace: str, value: dict | None) -> None:
    conv = ConversationRepo(db).get(conv_id)
    db.refresh(conv)
    current = _notes_dict(parse_notes(conv.notes), key)
    current = {name: entry for name, entry in current.items() if name != marketplace}
    conv.notes = merge_notes(conv.notes, {key: {**current, **({marketplace: value} if value else {})}})
    db.commit()


def _record(db: Session, conv_id: str, marketplace: str, value: dict) -> None:
    _store(db, conv_id, "marketplaceDelists", marketplace, value)


MARKS = ("listed", "sold", "not_listed")


def mark(db: Session, conv_id: str, marketplace: str, state: str, url: str | None = None) -> dict:
    """Record what the seller says about one marketplace. The marketplace itself is not touched."""
    if marketplace not in MARKETPLACES or state not in MARKS:
        raise DelistError(400, "Name a marketplace and what to mark it as.")
    if _running.locked():
        raise DelistError(409, "A delist is running. Wait for it to finish.")
    known = listings(db, conv_id).get(marketplace) or {}
    # Full precision: a sale reported later in the same second must still count as newer.
    value: dict = {"state": state, "at": datetime.now(UTC).isoformat()}
    if state == "listed":
        clean = str(url or "").strip()
        item_id = listing_id(marketplace, clean)
        if not item_id:
            raise DelistError(400, f"Paste the {marketplace.title()} listing link, as it appears in the browser's address bar.")
        value.update(url=clean, listing_id=item_id)
    else:
        value.update(url=known.get("url"), listing_id=known.get("listing_id"))
    conv = ConversationRepo(db).get(conv_id)
    binding = _notes_dict(_notes_dict(parse_notes(conv.notes), "nativeMarketplaceDrafts"), marketplace)
    if binding.get("state") in {"saving", "publishing", "unknown"}:
        raise DelistError(409, f"Check the {marketplace.title()} result first; Anteroom is still waiting to confirm it.")
    if state == "not_listed" and binding.get("state") in {"published", "pending_review"}:
        _store(db, conv_id, "nativeMarketplaceDrafts", marketplace, None)
    _store(db, conv_id, "marketplaceMarks", marketplace, value)
    # Marking listed or not listed starts the record over; a sale keeps how the listing ended.
    if state != "sold":
        _store(db, conv_id, "marketplaceDelists", marketplace, None)
    return listings(db, conv_id)


async def _delete_ebay(item_id: str) -> dict:
    from vendoo_studio.services.ebay_client import EbayClient, EbayFailure

    client = await EbayClient.connect()
    try:
        await client.trading("EndFixedPriceItem", {"ItemID": item_id, "EndingReason": "NotAvailable"})
    except EbayFailure as exc:
        # 1047: the listing has already ended. Anything else is the seller's to check.
        if "1047" in str(exc):
            return {"ok": True, "state": "deleted", "already": True}
        raise
    return {"ok": True, "state": "deleted"}


async def _delete_etsy(item_id: str) -> dict:
    from vendoo_studio.services.etsy_listings import connected_shop_id

    shop_id = connected_shop_id()
    try:
        remote = await marketplace_accounts.etsy_api("GET", f"listings/{item_id}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return {"ok": True, "state": "deleted", "already": True}
        raise
    if str(remote.get("shop_id")) != shop_id:
        raise ValueError("This Etsy listing belongs to another shop. Connect the shop that owns it.")
    if remote.get("state") == "sold_out":
        raise ValueError("This Etsy listing has sold out. It was not deleted.")
    await marketplace_accounts.etsy_api("DELETE", f"listings/{item_id}")
    return {"ok": True, "state": "deleted"}


def _failure_text(marketplace: str, exc: Exception) -> str:
    from vendoo_studio.services.etsy_listings import _error_text

    if marketplace == "etsy":
        return _error_text(exc)
    if isinstance(exc, browser_bridge.BrowserBridgeError):
        return "Chrome did not confirm the delete. Delist again to check; it is safe to repeat."
    return str(exc) or "The marketplace did not confirm the delete. Delist again to check; it is safe to repeat."


async def delist(db: Session, conv_id: str, marketplaces: list[str], confirm: bool) -> dict:
    from vendoo_studio.routes.extension import extension_manager

    names = list(dict.fromkeys(str(m).strip().lower() for m in marketplaces if str(m or "").strip()))
    if confirm is not True:
        raise DelistError(400, "Confirm the delist before anything is deleted.")
    if not names or any(name not in MARKETPLACES for name in names):
        raise DelistError(400, "Name the marketplaces to delist.")
    known = listings(db, conv_id)
    for name in names:
        entry = known.get(name)
        if not entry or not entry["listing_id"]:
            raise DelistError(409, f"Anteroom has no {name} listing link for this item.")
        if entry["sold_here"]:
            raise DelistError(409, f"This item sold on {name}. That listing is never deleted.")
    if BROWSER_MARKETPLACES & set(names) and not extension_manager.connected:
        raise DelistError(409, "Connect Chrome first.")
    if _running.locked():
        raise DelistError(409, "Another delist is running. Wait for it to finish.")
    async with _running:
        with start_gate():
            if JobRepo(db).get_active():
                raise DelistError(409, "Another automation job is queued or running. Wait for it to finish.")
        for name in names:
            entry = known[name]
            base = {"listing_id": entry["listing_id"], "url": entry["url"], "at": _now()}
            _record(db, conv_id, name, {**base, "state": "deleting"})
            try:
                if name == "ebay":
                    result = await _delete_ebay(entry["listing_id"])
                elif name == "etsy":
                    result = await _delete_etsy(entry["listing_id"])
                else:
                    result = await browser_bridge.request(SimpleNamespace(id=None), "marketplace.delete", {
                        "marketplace": name, "listing_id": entry["listing_id"], "confirm": True,
                    }, timeout=90)
                if result.get("ok") is True and result.get("state") == "deleted":
                    _record(db, conv_id, name, {**base, "state": "deleted", "error": None, "at": _now()})
                else:
                    _record(db, conv_id, name, {**base, "state": "failed", "at": _now(),
                                               "error": str(result.get("error") or "The marketplace did not confirm the delete.")[:300]})
            except Exception as exc:  # noqa: BLE001 - recorded per marketplace for the seller
                _record(db, conv_id, name, {**base, "state": "failed", "at": _now(), "error": _failure_text(name, exc)[:300]})
    return listings(db, conv_id)
