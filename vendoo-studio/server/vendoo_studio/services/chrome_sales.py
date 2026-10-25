"""Notice sales on marketplaces that sign in through Chrome.

eBay and Etsy push a sale through the relay. For the others the extension
reads the seller's sold list, or the live closet, in a few requests however
many listings are still up. Shops take turns, and the gap before the next
check is between four and twelve minutes. Anteroom sends every listing it still has live
there. A sold item counts only when Anteroom still has it live on that
marketplace, so a sale Anteroom already knows, or one it never listed, is not
reported. The extension returns listing IDs only. Nothing here delists.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from types import SimpleNamespace

from vendoo_studio.services.relay_sales import record_sale
from vendoo_studio.services.user_settings import sale_detection_enabled

log = logging.getLogger("vendoo_studio.chrome_sales")

CHROME_MARKETS = ("poshmark", "mercari", "depop", "sellwild")
POLL_INTERVAL_S = 30
MIN_GAP_S = 4 * 60
MAX_GAP_S = 12 * 60
MAX_CHECKED = 5000
MAX_REPORTED = 40

# Matches the IDs marketplace_delist reads from each listing link.
_LISTING_ID = {
    "poshmark": re.compile(r"^[a-f0-9]{24}$"),
    "mercari": re.compile(r"^m\d{6,}$"),
    "depop": re.compile(r"^[a-z0-9][a-z0-9-]{0,200}$"),
    "sellwild": re.compile(r"^\d{1,15}$"),
}
_HOST = {"poshmark": "poshmark.com", "mercari": "mercari.com", "depop": "depop.com", "sellwild": "sellwild.com"}

_task: asyncio.Task | None = None
_next: dict[str, float] = {}
_errors: dict[str, str] = {}


def check_errors() -> dict[str, str]:
    return dict(_errors)


def clear_check_error(marketplace: str) -> None:
    _errors.pop(marketplace, None)


def arm_next(marketplace: str, now: float) -> float:
    """The next check for this shop, somewhere between four and twelve minutes out."""
    due = now + random.uniform(MIN_GAP_S, MAX_GAP_S)
    _next[marketplace] = due
    return due


def normalize_sales(marketplace: str, raw: object) -> list[str]:
    """Keep listing IDs that match this marketplace. Drop everything else."""
    if marketplace not in _LISTING_ID or not isinstance(raw, dict) or raw.get("ok") is not True:
        return []
    sales = raw.get("sales")
    if not isinstance(sales, list):
        return []
    pattern = _LISTING_ID[marketplace]
    found: list[str] = []
    for item in sales[:MAX_REPORTED]:
        listing = item.get("listing_id") if isinstance(item, dict) else None
        if isinstance(listing, str) and pattern.fullmatch(listing) and listing not in found:
            found.append(listing)
    return found


def live_listing_ids(marketplace: str, *, session_factory=None) -> list[str]:
    """Listing IDs Anteroom has live on this marketplace: not sold, delisted or marked not listed."""
    from vendoo_studio.models.conversation import Conversation
    from vendoo_studio.services.marketplace_delist import listings

    if session_factory is None:
        from vendoo_studio.database import SessionLocal

        session_factory = SessionLocal
    found: list[str] = []
    with session_factory() as db:
        rows = db.query(Conversation.id).filter(Conversation.notes.contains(_HOST[marketplace])).all()
        for (conv_id,) in rows:
            entry = listings(db, conv_id).get(marketplace) or {}
            listing_id = entry.get("listing_id")
            if listing_id and entry.get("state") == "live" and not entry.get("sold_here") and listing_id not in found:
                found.append(listing_id)
    return found


def record_reported_sales(marketplace: str, listing_ids: list[str], *, session_factory=None) -> list[str]:
    """Store each sold listing once. Returns the new sale IDs."""
    if session_factory is None:
        from vendoo_studio.database import SessionLocal

        session_factory = SessionLocal
    created: list[str] = []
    with session_factory() as db:
        for listing_id in listing_ids:
            sale = record_sale(
                db,
                marketplace=marketplace,
                order_id=listing_id,
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
    live = await asyncio.to_thread(live_listing_ids, marketplace)
    listing_ids = live[:MAX_CHECKED]
    if not listing_ids:
        clear_check_error(marketplace)
        return
    try:
        raw = await browser_bridge.request(
            SimpleNamespace(id=None),
            "marketplace.sales",
            {"marketplace": marketplace, "listing_ids": listing_ids},
            timeout=90,
        )
    except browser_bridge.BrowserBridgeError:
        return
    if not isinstance(raw, dict) or raw.get("skipped") is True:
        return
    if raw.get("ok") is not True:
        _errors[marketplace] = "Couldn't check sales. Sign in again in Chrome if this keeps happening."
        return
    clear_check_error(marketplace)
    sold = [listing_id for listing_id in normalize_sales(marketplace, raw) if listing_id in live]
    if not sold:
        return
    created = await asyncio.to_thread(record_reported_sales, marketplace, sold)
    if not created:
        return
    try:
        await asyncio.to_thread(_notify_sales, created)
    except Exception:  # noqa: BLE001 - a missed banner must not drop the recorded sale
        log.debug("could not show the sale notification", exc_info=True)


async def check_due() -> None:
    """Check one shop. The others wait for their own turn, on uneven gaps."""
    now = time.time()
    for marketplace in CHROME_MARKETS:
        if not sale_detection_enabled(marketplace):
            clear_check_error(marketplace)
            _next.pop(marketplace, None)
            continue
        if _next.get(marketplace, 0) > now:
            continue
        arm_next(marketplace, now)
        try:
            await check_marketplace(marketplace)
        except Exception:  # noqa: BLE001 - one marketplace must not stop the others
            log.debug("could not check %s sales", marketplace, exc_info=True)
            _errors[marketplace] = "Couldn't check sales. Sign in again in Chrome if this keeps happening."
        return


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
