"""Seller-confirmed delisting: delete an item's live listings on named marketplaces.

Delist means the listing is removed from the marketplace. eBay has no delete,
so its listing is ended; Sellwild gives sellers only its own delist. The
marketplace where the item sold is never touched. Nothing here runs on a
timer, a queue, or a retry: each call names its marketplaces and carries the
seller's confirmation. Deleting is safe to repeat, because every marketplace
step reads the listing first and reports one that is already gone as deleted.
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


def _sold_on(db: Session, conv_id: str, notes: dict) -> set[str]:
    sold = {row.marketplace for row in db.query(MarketplaceSale).filter(MarketplaceSale.conversation_id == conv_id)}
    sale = notes.get("vendooSale") if isinstance(notes.get("vendooSale"), dict) else {}
    if sale.get("marketplace"):
        sold.add(str(sale["marketplace"]).lower())
    dates = notes.get("vendooDates") if isinstance(notes.get("vendooDates"), dict) else {}
    sold_by = dates.get("soldByMarketplace") if isinstance(dates.get("soldByMarketplace"), dict) else {}
    sold.update(str(name).lower() for name, when in sold_by.items() if when)
    return sold


def listings(db: Session, conv_id: str) -> dict:
    """Each marketplace listing Anteroom knows for this item, and what Delist would do."""
    conv = ConversationRepo(db).get(conv_id)
    if not conv:
        raise DelistError(404, "Conversation not found")
    notes = parse_notes(conv.notes)
    urls = notes.get("vendooListingUrls") if isinstance(notes.get("vendooListingUrls"), dict) else {}
    native = notes.get("nativeMarketplaceDrafts") if isinstance(notes.get("nativeMarketplaceDrafts"), dict) else {}
    delists = notes.get("marketplaceDelists") if isinstance(notes.get("marketplaceDelists"), dict) else {}
    sold = _sold_on(db, conv_id, notes)
    result = {}
    for marketplace in MARKETPLACES:
        binding = native.get(marketplace) if isinstance(native.get(marketplace), dict) else {}
        candidates = [(binding.get("listing_url"), "anteroom"), (urls.get(marketplace), "vendoo")]
        url, source = next(((u, s) for u, s in candidates if listing_id(marketplace, u)), (None, None))
        record = delists.get(marketplace) if isinstance(delists.get(marketplace), dict) else {}
        if not url and not record:
            continue
        item_id = listing_id(marketplace, url) or record.get("listing_id")
        # A newer listing than the one deleted is live again.
        if record and item_id and record.get("listing_id") != item_id:
            record = {}
        result[marketplace] = {"url": url or record.get("url"), "listing_id": item_id, "source": source,
                               "sold_here": marketplace in sold, "state": record.get("state") or "live",
                               "error": record.get("error"), "at": record.get("at")}
    return result


def _record(db: Session, conv_id: str, marketplace: str, value: dict) -> None:
    conv = ConversationRepo(db).get(conv_id)
    db.refresh(conv)
    delists = parse_notes(conv.notes).get("marketplaceDelists")
    delists = delists if isinstance(delists, dict) else {}
    conv.notes = merge_notes(conv.notes, {"marketplaceDelists": {**delists, marketplace: value}})
    db.commit()


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
