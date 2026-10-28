"""Notice sales on marketplaces that sign in through Chrome.

eBay and Etsy push a sale through the relay. Depop and Poshmark are read from
the seller's own recent sales, and Mercari and Sellwild from listings Anteroom
already knows. The extension returns listing and order IDs only. Nothing here
delists.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from types import SimpleNamespace

from vendoo_studio.services.relay_sales import listing_id_from_url, record_sale
from vendoo_studio.services.user_settings import sale_detection_enabled

log = logging.getLogger("vendoo_studio.chrome_sales")

CHROME_MARKETS = ("depop", "poshmark", "mercari", "sellwild")
# Markets whose seller page lists recent sales in one read.
FEED_MARKETS = frozenset({"depop", "poshmark"})
POLL_INTERVAL_S = 30
DUE_AFTER_S = 300
BATCH = 15

_LISTING_ID = {
    "poshmark": re.compile(r"^[a-f0-9]{24}$"),
    "depop": re.compile(r"^\d{5,12}$"),
    "mercari": re.compile(r"^m\d{8,14}$"),
    "sellwild": re.compile(r"^\d{1,12}$"),
}
_ORDER_ID = {
    "poshmark": re.compile(r"^[a-f0-9]{24}$"),
    "depop": re.compile(r"^\d{1,20}$"),
    "mercari": re.compile(r"^m\d{8,14}$"),
    "sellwild": re.compile(r"^\d{1,12}$"),
}
_HOST = {"mercari": "mercari.com", "sellwild": "sellwild.com"}

_task: asyncio.Task | None = None
_last: dict[str, float] = {}
_errors: dict[str, str] = {}
_cursor: dict[str, int] = {}


def check_errors() -> dict[str, str]:
    return dict(_errors)


def clear_check_error(marketplace: str) -> None:
    _errors.pop(marketplace, None)


def normalize_sales(marketplace: str, raw: object) -> list[tuple[str, str]]:
    """Keep listing and order IDs that match this marketplace. Drop everything else."""
    if marketplace not in _LISTING_ID or not isinstance(raw, dict) or raw.get("ok") is not True:
        return []
    sales = raw.get("sales")
    if not isinstance(sales, list):
        return []
    listing_re, order_re = _LISTING_ID[marketplace], _ORDER_ID[marketplace]
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in sales[:40]:
        if not isinstance(item, dict):
            continue
        listing = item.get("listing_id")
        order = item.get("order_id") or listing
        if not isinstance(listing, str) or not isinstance(order, str):
            continue
        if not listing_re.fullmatch(listing) or not order_re.fullmatch(order):
            continue
        key = (order, listing)
        if key in seen:
            continue
        seen.add(key)
        found.append(key)
    return found


def _cursor_key(marketplace: str) -> str:
    return f"chrome_sales_cursor_{marketplace}"


def load_cursor(marketplace: str) -> int:
    """Where the last pass stopped, so a restart does not re-check the same first batch forever."""
    from vendoo_studio.services.user_settings import read_settings

    value = read_settings().get(_cursor_key(marketplace))
    return value if isinstance(value, int) and value >= 0 else 0


def save_cursor(marketplace: str, position: int) -> None:
    from vendoo_studio.services.user_settings import update_settings

    def mutator(payload: dict) -> None:
        payload[_cursor_key(marketplace)] = position

    update_settings(mutator)


def listing_ids_to_check(marketplace: str) -> list[str]:
    """Known Mercari or Sellwild listing IDs, a few at a time."""
    from vendoo_studio.database import SessionLocal
    from vendoo_studio.models.conversation import Conversation
    from vendoo_studio.services.vendoo_import import parse_notes

    host = _HOST.get(marketplace)
    if not host:
        return []
    with SessionLocal() as db:
        rows = db.query(Conversation.notes).filter(Conversation.notes.contains(host)).all()
    found: list[str] = []
    seen: set[str] = set()
    for (notes,) in rows:
        parsed = parse_notes(notes) if isinstance(notes, str) else {}
        urls: list[str] = []
        reported = parsed.get("vendooListingUrls") or {}
        if isinstance(reported, dict) and isinstance(reported.get(marketplace), str):
            urls.append(reported[marketplace])
        native = parsed.get("nativeMarketplaceDrafts") or {}
        if isinstance(native, dict):
            binding = native.get(marketplace)
            if isinstance(binding, dict) and isinstance(binding.get("listing_url"), str):
                urls.append(binding["listing_url"])
        for url in urls:
            listing_id = listing_id_from_url(marketplace, url)
            if listing_id and listing_id not in seen:
                seen.add(listing_id)
                found.append(listing_id)
    if not found:
        return []
    start = _cursor.get(marketplace) or load_cursor(marketplace)
    start %= len(found)
    _cursor[marketplace] = start + BATCH
    save_cursor(marketplace, _cursor[marketplace])
    ordered = found[start:] + found[:start]
    return ordered[:BATCH]


def record_reported_sales(marketplace: str, pairs: list[tuple[str, str]], *, session_factory=None) -> list[str]:
    """Store each sold line once. Returns the new sale IDs."""
    if session_factory is None:
        from vendoo_studio.database import SessionLocal

        session_factory = SessionLocal
    created: list[str] = []
    with session_factory() as db:
        for order_id, listing_id in pairs:
            sale = record_sale(
                db,
                marketplace=marketplace,
                order_id=order_id,
                line_id=listing_id,
                listing_id=listing_id,
                quantity=None,
                sold_at=None,
            )
            if sale:
                created.append(sale.id)
    return created


async def check_marketplace(marketplace: str) -> None:
    from vendoo_studio.services import browser_bridge
    from vendoo_studio.services.marketplace_accounts import _notify_sales

    if marketplace not in CHROME_MARKETS or not sale_detection_enabled(marketplace):
        clear_check_error(marketplace)
        return
    listing_ids = [] if marketplace in FEED_MARKETS else await asyncio.to_thread(listing_ids_to_check, marketplace)
    if marketplace not in FEED_MARKETS and not listing_ids:
        clear_check_error(marketplace)
        return
    try:
        raw = await browser_bridge.request(
            SimpleNamespace(id=None),
            "marketplace.sales",
            {"marketplace": marketplace, "listing_ids": listing_ids},
            timeout=40,
        )
    except browser_bridge.BrowserBridgeError:
        return
    if not isinstance(raw, dict) or raw.get("skipped") is True:
        return
    if raw.get("ok") is not True:
        _errors[marketplace] = "Couldn't check sales. Sign in again in Chrome if this keeps happening."
        return
    clear_check_error(marketplace)
    pairs = normalize_sales(marketplace, raw)
    if not pairs:
        return
    created = await asyncio.to_thread(record_reported_sales, marketplace, pairs)
    if not created:
        return
    try:
        await asyncio.to_thread(_notify_sales, created)
    except Exception:  # noqa: BLE001 - a missed banner must not drop the recorded sale
        log.debug("could not show the sale notification", exc_info=True)


async def check_due() -> None:
    now = time.time()
    for marketplace in CHROME_MARKETS:
        if not sale_detection_enabled(marketplace):
            clear_check_error(marketplace)
            continue
        if now - _last.get(marketplace, 0) < DUE_AFTER_S:
            continue
        _last[marketplace] = now
        try:
            await check_marketplace(marketplace)
        except Exception:  # noqa: BLE001 - one marketplace must not stop the others
            log.debug("could not check %s sales", marketplace, exc_info=True)
            _errors[marketplace] = "Couldn't check sales. Sign in again in Chrome if this keeps happening."


async def _watch() -> None:
    while True:
        await asyncio.sleep(POLL_INTERVAL_S)
        try:
            await check_due()
        except Exception:  # noqa: BLE001 - one bad pass must not stop the watcher
            log.exception("Sale check failed")


def start_chrome_sales() -> asyncio.Task:
    """Check Chrome marketplaces on an interval. Call from the running event loop."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(_watch())
    return _task


def stop_chrome_sales() -> None:
    if _task is not None and not _task.done():
        _task.cancel()
