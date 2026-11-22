"""Follow a sale until the item is off every other marketplace.

An item still listed elsewhere after it sold can sell twice. The delist that
follows a sale can fail: an expired sign-in, Chrome away, a marketplace that
refuses or does not answer. This module finds each recently sold-out item that
Anteroom still has listed on another marketplace (``still_listed``), so the
app can alert the seller until it is resolved. When the seller turned on
**Delist the other marketplaces**, ``run`` also delists those listings again,
on a widening schedule, until each marketplace confirms it.

It acts only on a sale a marketplace reported, for an order still paid, and
never on the marketplace that sold, an item with stock left, or a sale older
than ``WINDOW``. Each sale's targets are pinned by listing ID the first time it
is seen (``pin_sale``), leaving out any listing Anteroom knows went up after the
sale; a relist or any other changed listing is never delisted here, only shown.
It only delists: an already absent listing is recorded as absent, never as a
sale, and no sale is created or changed here.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from anteroom.models.conversation import Conversation
from anteroom.models.marketplace_sale import PAID_STATUSES, MarketplaceSale

log = logging.getLogger("anteroom.sale_delist_followup")

# Sales this recent are followed. Older history is the seller's to review.
WINDOW = timedelta(days=14)
# The first retry after a failed delist, doubling up to the cap.
FIRST_RETRY = timedelta(minutes=5)
MAX_RETRY = timedelta(hours=1)
# A delete left "deleting" this long stopped before it answered.
STALE_DELETING = timedelta(minutes=10)
# The follow-up looks at a seller's sales at most this often.
SWEEP_GAP_S = 60
# Alert the operator on the first failed try, then every this many tries.
ALERT_EVERY = 6

_next: dict[str, float] = {}
_running: set[str] = set()
_tasks: set[asyncio.Task] = set()


def _now() -> datetime:
    return datetime.now(UTC)


def _time(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    stamp = _time(value)
    return stamp.isoformat() if stamp else None


def retry_after(attempts: int) -> timedelta:
    # Bound the doubling first: a timedelta overflows long before the attempts stop.
    return min(FIRST_RETRY * 2 ** min(max(attempts - 1, 0), 10), MAX_RETRY)


def _went_up_after(name: str, item_id: str, native: dict, marks: dict, sold_at: datetime | None) -> bool:
    """Whether Anteroom has evidence this listing was put up after the sale: a relist, not a copy the sale left."""
    from anteroom.services.marketplace_identity import listing_id

    if sold_at is None:
        return True
    binding = native.get(name)
    if isinstance(binding, dict) and item_id in {binding.get("listing_id"), listing_id(name, binding.get("listing_url"))}:
        listed = _time(binding.get("listed_at"))
        if listed and listed > sold_at:
            return True
    mark = marks.get(name)
    if isinstance(mark, dict) and mark.get("state") == "listed" and listing_id(name, mark.get("url")) == item_id:
        marked = _time(mark.get("at"))
        if marked and marked > sold_at:
            return True
    return False


def still_listed(db: Session) -> list[dict]:
    """Sold-out items sold recently that Anteroom still has listed elsewhere, newest sale first.

    ``followed`` says whether the follow-up may delist that listing: it is the one
    the sale left up, by listing ID, never one put up after the sale.
    """
    from anteroom.repositories.marketplace_listings import MarketplaceListingRepo
    from anteroom.services.marketplace_delist import MARKETPLACES, listings, sale_pins
    from anteroom.services.sale_inventory import stock_after_sale
    from anteroom.services.vendoo_import import parse_notes

    since = (_now() - WINDOW).replace(tzinfo=None)
    sales = (db.query(MarketplaceSale)
             .filter(MarketplaceSale.order_status.in_(PAID_STATUSES), MarketplaceSale.conversation_id.isnot(None),
                     MarketplaceSale.source.in_(("marketplace", "seller", "vendoo")),
                     func.coalesce(MarketplaceSale.sold_at, MarketplaceSale.received_at) >= since)
             .order_by(MarketplaceSale.received_at.desc()).all())
    found: dict[str, dict] = {}
    for sale in sales:
        conv_id = str(sale.conversation_id)
        detected = sale.source == "marketplace" and sale.marketplace in MARKETPLACES
        # A sale the seller recorded (in person included) or imported from Vendoo is shown; only a reported one is delisted automatically.
        if conv_id in found or not detected and sale.source not in {"seller", "vendoo"}:
            continue
        if stock_after_sale(db, conv_id, sale.marketplace, sale.listing_id)["status"] != "sold_out":
            continue
        conv = db.get(Conversation, conv_id)
        if conv is None:
            continue
        notes = parse_notes(conv.notes)
        native = MarketplaceListingRepo(db).for_conversation(conv_id)
        known = listings(db, conv_id, conv=conv, native=native)
        pinned = sale_pins(notes).get(sale.id)
        marks = notes.get("marketplaceMarks")
        marks = marks if isinstance(marks, dict) else {}
        sold_at = _time(sale.sold_at or sale.received_at)
        left = []
        for name in MARKETPLACES:
            entry = known.get(name)
            if (name == sale.marketplace or not entry or not entry.get("listing_id") or entry.get("sold_here")
                    or entry.get("state") == "deleted"):
                continue
            item_id = str(entry["listing_id"])
            followed = ((pinned is None or pinned.get(name) == item_id)
                        and not _went_up_after(name, item_id, native, marks, sold_at))
            left.append({"marketplace": name, "listingId": item_id, "state": entry.get("state"),
                         "error": entry.get("error"), "at": entry.get("at"), "attempts": entry.get("attempts") or 0,
                         "url": entry.get("url"), "followed": followed})
        if left:
            found[conv_id] = {"conversationId": conv_id, "title": conv.title, "saleId": sale.id,
                              "soldOn": sale.marketplace, "soldAt": _iso(sale.sold_at or sale.received_at),
                              "detected": detected, "pinned": pinned is not None,
                              "stillListed": left}
    return list(found.values())


def _sweep(db: Session, *, act: bool) -> list[tuple[str, str, dict[str, str]]]:
    """Pin what each new sale left up, then list the pinned listings due another delist.

    Pins are recorded whether or not the seller turned the follow-up on, so turning
    it on later never reaches a listing put up after the sale.
    """
    from anteroom.repositories.queries import JobRepo
    from anteroom.services.marketplace_delist import pin_sale

    items = [item for item in still_listed(db) if item["detected"]]
    for item in items:
        if not item["pinned"]:
            targets = {entry["marketplace"]: entry["listingId"] for entry in item["stillListed"] if entry["followed"]}
            pinned = pin_sale(db, item["conversationId"], item["saleId"], targets)
            for entry in item["stillListed"]:
                entry["followed"] = entry["followed"] and pinned.get(entry["marketplace"]) == entry["listingId"]
    if not act:
        return []
    busy = {job.conversation_id for job in JobRepo(db).get_running()
            if str(job.current_step or "") == "native_delist"}
    now = _now()
    due = []
    for item in items:
        if item["conversationId"] in busy:
            continue
        targets = {}
        for entry in item["stillListed"]:
            if not entry["followed"]:
                continue
            at = _time(entry["at"])
            if (entry["state"] == "live"
                    or entry["state"] == "failed" and (at is None or at + retry_after(entry["attempts"]) <= now)
                    or entry["state"] == "deleting" and (at is None or at + STALE_DELETING <= now)):
                targets[entry["marketplace"]] = entry["listingId"]
        if targets:
            due.append((item["conversationId"], item["saleId"], targets))
    return due


def _seller_key() -> str:
    from anteroom.services.service_context import current

    context = current()
    return context.seller_id if context else ""


def _enabled() -> bool:
    from anteroom.services.mac_workspace import account_shown
    from anteroom.services.user_settings import autodelist_enabled

    # While the Mac's window shows the account, that account follows its own sales.
    return autodelist_enabled(fresh=True) and not account_shown()


async def run(*, session_factory=None) -> None:
    """Delist again what a sale left listed elsewhere, when the seller turned that on.

    At most once a minute per seller, one item after another. The seller's setting
    is read again before each item, and each delist names the exact listing the
    sale left up. A delist that fails is recorded on the item and tried again
    later; nothing here raises.
    """
    key = _seller_key()
    now = time.monotonic()
    if key in _running or now < _next.get(key, 0):
        return
    _running.add(key)
    _next[key] = now + SWEEP_GAP_S
    try:
        enabled = await asyncio.to_thread(_enabled)
        if session_factory is None:
            from anteroom.services.service_context import current

            context = current()
            if context is not None:
                session_factory = context.session_factory
            else:
                from anteroom.database import SessionLocal

                session_factory = SessionLocal

        def sweep():
            with session_factory() as db:
                return _sweep(db, act=enabled)

        for conv_id, sale_id, targets in await asyncio.to_thread(sweep):
            if not await asyncio.to_thread(_enabled):
                return
            await _follow(conv_id, sale_id, targets)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - the next sweep tries again
        log.exception("Could not follow up on sold items still listed elsewhere")
    finally:
        _running.discard(key)


async def _follow(conv_id: str, sale_id: str, targets: dict[str, str]) -> None:
    """Delist server-side marketplaces (eBay, Etsy, and cloud-connected) apart from Chrome-only ones."""
    from anteroom.services import cloud_sessions
    from anteroom.services.marketplace_delist import BROWSER_MARKETPLACES

    def _cloud_ready() -> set[str]:
        return {
            name for name in targets
            if name in BROWSER_MARKETPLACES and cloud_sessions.has_active_session(name)
        }

    cloud_ready = await asyncio.to_thread(_cloud_ready)
    server_side = {
        name: item for name, item in targets.items()
        if name not in BROWSER_MARKETPLACES or name in cloud_ready
    }
    chrome_only = {
        name: item for name, item in targets.items()
        if name in BROWSER_MARKETPLACES and name not in cloud_ready
    }
    for group in (server_side, chrome_only):
        if group:
            await _follow_group(conv_id, sale_id, group)


async def _follow_group(conv_id: str, sale_id: str, targets: dict[str, str]) -> None:
    from anteroom.database import in_worker_session
    from anteroom.services import sale_quantity_sync
    from anteroom.services.marketplace_delist import DelistError, delist

    names = list(targets)
    try:
        result = await delist(conv_id, names, confirm=True, expected=targets, sale_id=sale_id)
    except DelistError as exc:
        # Chrome away, another job running, listing mode, the listing or the sale changed: nothing was tried.
        log.info("Delist after a sale waits for item %s on %s: %s", conv_id, ", ".join(names), exc)
        return
    except Exception as exc:  # noqa: BLE001 - HTTPException from listing mode and the like
        log.info("Delist after a sale could not start for item %s: %s", conv_id, exc)
        return
    try:
        await in_worker_session(
            sale_quantity_sync.record_delist_outcomes,
            [sale_id],
            conv_id,
            dict(targets),
            result,
        )
    except Exception:  # noqa: BLE001 - tracker recording must not break follow-up loop
        log.debug("Could not record follow-up delist outcomes for %s", sale_id, exc_info=True)
    for name in names:
        entry = result.get(name) or {}
        if entry.get("state") != "failed":
            continue
        attempts = entry.get("attempts") or 1
        if attempts == 1 or attempts % ALERT_EVERY == 0:
            # On the hosted app an error emails the operator.
            log.error("A sold item is still listed on %s after %d delist tries: %s", name, attempts,
                      entry.get("error") or "the marketplace did not confirm")


def start() -> None:
    """Run the follow-up for the current seller beside the caller, without waiting for it.

    The task copies the caller's seller context, so a hosted sweep stays in that seller's rows.
    """
    if _seller_key() in _running or time.monotonic() < _next.get(_seller_key(), 0):
        return
    task = asyncio.get_running_loop().create_task(run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
