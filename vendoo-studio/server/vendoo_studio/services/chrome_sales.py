"""Notice sales on marketplaces that sign in through Chrome.

eBay and Etsy push a sale through the relay. Depop, Poshmark, Mercari,
Sellwild and Vinted are read from their own order history, with the amounts
each reports (see ``order_history``): a listing that only looks sold is never
a sale. Shops take turns, and the gap before the next check is between four
and twelve minutes. Delisting the other marketplaces happens only when the
seller turned that option on.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from typing import cast

from sqlalchemy.orm import Session

from vendoo_studio.services.user_settings import sale_detection_enabled

log = logging.getLogger("vendoo_studio.chrome_sales")

CHROME_MARKETS = ("poshmark", "mercari", "depop", "sellwild", "vinted")
POLL_INTERVAL_S = 30
MIN_GAP_S = 4 * 60
MAX_GAP_S = 12 * 60

# Matches the IDs marketplace_delist reads from each listing link.
_LISTING_ID = {
    "poshmark": re.compile(r"^[a-f0-9]{24}$"),
    "mercari": re.compile(r"^m\d{6,}$"),
    "depop": re.compile(r"^[a-z0-9][a-z0-9-]{0,200}$"),
    "sellwild": re.compile(r"^\d{1,15}$"),
    "vinted": re.compile(r"^\d{1,15}$"),
}

# An item opened in Anteroom reads its own listings at most this often per
# shop. Browsing items must not read a marketplace more than the timed checks do.
ITEM_GAP_S = 60

_task: asyncio.Task | None = None
_next: dict[str, float] = {}
_errors: dict[str, str] = {}
_item_read: dict[str, float] = {}


def _market_key(marketplace: str) -> str:
    from vendoo_studio.services.service_context import current

    context = current()
    return f"{context.seller_id}:{marketplace}" if context else marketplace


def check_errors() -> dict[str, str]:
    return {marketplace: _errors[_market_key(marketplace)] for marketplace in CHROME_MARKETS
            if _market_key(marketplace) in _errors}


def clear_check_error(marketplace: str) -> None:
    _errors.pop(_market_key(marketplace), None)


def arm_next(marketplace: str, now: float) -> float:
    """The next check for this shop, somewhere between four and twelve minutes out."""
    due = now + random.uniform(MIN_GAP_S, MAX_GAP_S)
    _next[_market_key(marketplace)] = due
    return due


def _recorded_sales(db: Session, conv_ids, marketplace: str | None = None) -> dict[tuple[str, str], set[str | None]]:
    """The listing IDs Anteroom itself recorded a sale for, by conversation and marketplace."""
    from vendoo_studio.models.marketplace_sale import PAID_STATUSES, MarketplaceSale

    recorded: dict[tuple[str, str], set[str | None]] = {}
    if not conv_ids:
        return recorded
    query = db.query(MarketplaceSale.conversation_id, MarketplaceSale.marketplace, MarketplaceSale.listing_id).filter(
        MarketplaceSale.conversation_id.in_(list(conv_ids)), MarketplaceSale.order_status.in_(PAID_STATUSES))
    if marketplace:
        query = query.filter(MarketplaceSale.marketplace == marketplace)
    for conv_id, name, listing_id in query.all():
        recorded.setdefault((cast(str, conv_id), name), set()).add(listing_id)
    return recorded


def _needs_sale_check(entry: dict, listing_id: str, recorded: set[str | None]) -> bool:
    """False for a listing whose sale Anteroom recorded. A sale only the imported notes claim is still checked."""
    return entry.get("state") == "live" and listing_id not in recorded and None not in recorded


async def check_marketplace(marketplace: str) -> bool:
    """Read this shop's orders. False when Chrome could not check it."""
    if marketplace not in CHROME_MARKETS or not sale_detection_enabled(marketplace):
        clear_check_error(marketplace)
        return True
    return await _read_history(marketplace)


async def _read_history(marketplace: str, *, session_factory=None) -> bool:
    """Read this shop's orders. False when Chrome could not read them."""
    from vendoo_studio.services import order_history

    result = await order_history.sync(marketplace, session_factory=session_factory)
    if result.get("ok"):
        if result.get("unreadable"):
            _errors[_market_key(marketplace)] = "Some sales couldn't be read. Their saved records were kept."
        else:
            clear_check_error(marketplace)
        _item_read[_market_key(marketplace)] = time.time()
        return True
    if result.get("error"):
        _errors[_market_key(marketplace)] = result["error"]
    return False


def live_item_listings(db: Session, conv_id: str) -> dict[str, str]:
    """Listing IDs this item has on marketplaces Chrome reads, and whose sale Anteroom has not seen itself.

    A marketplace Anteroom recorded a sale for is settled. One that reads sold
    only from the imported notes is still checked, so that claim is confirmed
    on the marketplace instead of being believed.
    """
    from vendoo_studio.services.marketplace_delist import listings

    recorded = _recorded_sales(db, [conv_id])
    found: dict[str, str] = {}
    for marketplace, entry in listings(db, conv_id).items():
        listing_id = entry.get("listing_id")
        if marketplace not in CHROME_MARKETS or not listing_id:
            continue
        if _needs_sale_check(entry, listing_id, recorded.get((conv_id, marketplace), set())):
            found[marketplace] = listing_id
    return found


async def check_item(db: Session, conv_id: str, *, session_factory=None) -> dict:
    """Read this item's live listings now, so a sale shows while the seller looks at it.

    Opening an item brings each of its shops' next check forward to this item
    alone, which is what makes a sale read as live instead of waiting for the
    shop's own turn. A shop is read at most once a minute however many items
    the seller opens, and its timed checks keep their own schedule. A sale the
    imported notes claim is checked the same way, so Anteroom believes only
    what a marketplace tells it. Only the listing IDs travel, and nothing on
    any marketplace changes.
    """
    from vendoo_studio.services.extension_connection import current_extension

    extension_manager = current_extension()
    result: dict = {"ok": True, "checked": [], "recorded": [], "skipped": None}
    wanted = {name: listing_id for name, listing_id in live_item_listings(db, conv_id).items()
              if sale_detection_enabled(name)}
    if not wanted:
        result["skipped"] = "No live listing to check."
        return result
    if not extension_manager.connected:
        result.update(ok=False, skipped="Connect Chrome first.")
        return result
    now = time.time()
    due = {name: listing_id for name, listing_id in wanted.items()
           if now - _item_read.get(_market_key(name), 0) >= ITEM_GAP_S}
    if not due:
        result["skipped"] = "Just checked."
        return result
    for marketplace in due:
        before = await asyncio.to_thread(_line_ids, session_factory, conv_id)
        if await _read_history(marketplace, session_factory=session_factory):
            result["checked"].append(marketplace)
            after = await asyncio.to_thread(_line_ids, session_factory, conv_id)
            result["recorded"].extend(sorted(after - before))
    return result


def _line_ids(session_factory, conv_id: str) -> set[str]:
    from vendoo_studio.models.marketplace_sale import MarketplaceSale

    if session_factory is None:
        from vendoo_studio.database import SessionLocal

        session_factory = SessionLocal
    with session_factory() as db:
        return {row[0] for row in db.query(MarketplaceSale.id).filter(MarketplaceSale.conversation_id == conv_id).all()}


async def check_due() -> None:
    """Check one shop. The others wait for their own turn, on uneven gaps."""
    now = time.time()
    for marketplace in CHROME_MARKETS:
        if not sale_detection_enabled(marketplace):
            clear_check_error(marketplace)
            _next.pop(_market_key(marketplace), None)
            continue
        if _next.get(_market_key(marketplace), 0) > now:
            continue
        arm_next(marketplace, now)
        try:
            await check_marketplace(marketplace)
        except Exception:  # noqa: BLE001 - one marketplace must not stop the others
            log.debug("could not check %s sales", marketplace, exc_info=True)
            _errors[_market_key(marketplace)] = "Couldn't check sales. Sign in again in Chrome if this keeps happening."
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
