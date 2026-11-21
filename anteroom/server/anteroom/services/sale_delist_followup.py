"""Follow a sale until the item is off every other marketplace.

An item still listed elsewhere after it sold can sell twice. The delist that
follows a sale can fail: an expired sign-in, Chrome away, a marketplace that
refuses or does not answer. This module finds each recently sold-out item that
Anteroom still has listed on another marketplace (``still_listed``), so the
app can alert the seller until it is resolved. When the seller turned on
**Delist the other marketplaces**, ``run`` also delists those listings again,
on a widening schedule, for sales from the last two weeks.

It acts only on a sale a marketplace reported, for an order still paid, and
never on the marketplace that sold, an item with stock left, or a sale older
than ``WINDOW``. It only delists: an already absent listing is recorded as
absent, never as a sale, and no sale is created or changed here.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from anteroom.models.conversation import Conversation
from anteroom.models.marketplace_sale import OTHER_MARKETPLACE, PAID_STATUSES, MarketplaceSale

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
    exponent = max(attempts - 1, 0)
    if exponent >= (MAX_RETRY // FIRST_RETRY).bit_length():
        return MAX_RETRY
    return min(FIRST_RETRY * 2 ** exponent, MAX_RETRY)


def still_listed(db: Session) -> list[dict]:
    """Sold-out items sold recently that Anteroom still has listed elsewhere, newest sale first."""
    from anteroom.services.marketplace_delist import MARKETPLACES, listings
    from anteroom.services.sale_inventory import stock_after_sale

    since = (_now() - WINDOW).replace(tzinfo=None)
    sales = (db.query(MarketplaceSale)
             .filter(MarketplaceSale.order_status.in_(PAID_STATUSES), MarketplaceSale.conversation_id.isnot(None),
                     MarketplaceSale.source.in_(("marketplace", "seller")),
                     func.coalesce(MarketplaceSale.sold_at, MarketplaceSale.received_at) >= since)
             .order_by(MarketplaceSale.received_at.desc()).all())
    found: dict[str, dict] = {}
    for sale in sales:
        conv_id = str(sale.conversation_id)
        if conv_id in found or (sale.marketplace not in MARKETPLACES
                                and not (sale.source == "seller" and sale.marketplace == OTHER_MARKETPLACE)):
            continue
        if stock_after_sale(db, conv_id, sale.marketplace, sale.listing_id)["status"] != "sold_out":
            continue
        conv = db.get(Conversation, conv_id)
        if conv is None:
            continue
        known = listings(db, conv_id, conv=conv)
        left = [
            {"marketplace": name, "state": entry.get("state"), "error": entry.get("error"),
             "at": entry.get("at"), "attempts": entry.get("attempts") or 0, "url": entry.get("url")}
            for name in MARKETPLACES
            if name != sale.marketplace and (entry := known.get(name)) and entry.get("listing_id")
            and not entry.get("sold_here") and entry.get("state") != "deleted"
        ]
        if left:
            found[conv_id] = {"conversationId": conv_id, "title": conv.title, "saleId": sale.id,
                              "soldOn": sale.marketplace, "soldAt": _iso(sale.sold_at or sale.received_at),
                              "detected": sale.source == "marketplace", "stillListed": left}
    return list(found.values())


def _due(db: Session) -> list[tuple[str, str, list[str]]]:
    """Items whose remaining listings are due another delist, and which ones."""
    from anteroom.repositories.queries import JobRepo

    busy = {job.conversation_id for job in JobRepo(db).get_running()
            if str(job.current_step or "") == "native_delist"}
    now = _now()
    due = []
    for item in still_listed(db):
        if not item["detected"] or item["conversationId"] in busy:
            continue
        names = []
        for entry in item["stillListed"]:
            at = _time(entry["at"])
            if entry["state"] == "live":
                names.append(entry["marketplace"])
            elif entry["state"] == "failed" and (at is None or at + retry_after(entry["attempts"]) <= now):
                names.append(entry["marketplace"])
            elif entry["state"] == "deleting" and (at is None or at + STALE_DELETING <= now):
                names.append(entry["marketplace"])
        if names:
            due.append((item["conversationId"], item["saleId"], names))
    return due


def _seller_key() -> str:
    from anteroom.services.service_context import current

    context = current()
    return context.seller_id if context else ""


def _enabled() -> bool:
    from anteroom.services.mac_workspace import account_shown
    from anteroom.services.user_settings import autodelist_enabled

    # While the Mac's window shows the account, that account follows its own sales.
    return autodelist_enabled() and not account_shown()


async def run(*, session_factory=None) -> None:
    """Delist again what a sale left listed elsewhere, when the seller turned that on.

    At most once a minute per seller, one item after another. A delist that
    fails is recorded on the item and tried again later; nothing here raises.
    """
    key = _seller_key()
    now = time.monotonic()
    if key in _running or now < _next.get(key, 0):
        return
    _running.add(key)
    _next[key] = now + SWEEP_GAP_S
    try:
        if not await asyncio.to_thread(_enabled):
            return
        if session_factory is None:
            from anteroom.services.service_context import current

            context = current()
            if context is not None:
                session_factory = context.session_factory
            else:
                from anteroom.database import SessionLocal

                session_factory = SessionLocal

        def read_due():
            with session_factory() as db:
                return _due(db)

        for conv_id, sale_id, names in await asyncio.to_thread(read_due):
            await _follow(conv_id, sale_id, names)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - the next sweep tries again
        log.exception("Could not follow up on sold items still listed elsewhere")
    finally:
        _running.discard(key)


async def _follow(conv_id: str, sale_id: str, names: list[str]) -> None:
    from anteroom.services.marketplace_delist import DelistError, delist

    try:
        result = await delist(conv_id, names, confirm=True, followup_sale_id=sale_id)
    except DelistError as exc:
        # Chrome away, another job running, listing mode: nothing was tried.
        log.info("Delist after a sale waits for item %s on %s: %s", conv_id, ", ".join(names), exc)
        return
    except Exception as exc:  # noqa: BLE001 - HTTPException from listing mode and the like
        log.info("Delist after a sale could not start for item %s: %s", conv_id, exc)
        return
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
