"""Opt-in multi-quantity auto-adjustment after a sale and 60-day sale detection tracker.

Safety invariants preserved:
- Auto-adjust quantity is OFF by default (`sale_auto_adjust_quantity`).
- Cancelled, refunded, and cancellation-pending orders are never acted upon.
- Multi-quantity items with remaining stock are NEVER delisted; their remaining
  quantity is synced across live marketplaces, and on Mercari (which caps
  listing quantity at 1) a fresh single-quantity listing is relisted when
  stock remains after a Mercari sale and the tracked listing ID is swapped
  atomically.
- No DB session is ever held open across marketplace/extension network awaits.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from anteroom.models.conversation import Conversation, new_id
from anteroom.models.listing import Listing, ListingRevision
from anteroom.models.marketplace_listing import LIVE_STATES, MarketplaceListing
from anteroom.models.marketplace_sale import PAID_STATUSES, MarketplaceSale, SaleSyncEvent
from anteroom.services import auto_relist, listing_persistence
from anteroom.services.marketplace_delist import MARKETPLACES, DelistError, delist, listings
from anteroom.services.marketplace_identity import listing_id as extract_listing_id
from anteroom.services.vendoo_import import parse_notes

log = logging.getLogger("anteroom.sale_quantity_sync")

TRACKER_WINDOW = timedelta(days=60)
MERCARI_MAX_QUANTITY = 1
FIRST_RETRY = timedelta(minutes=5)
MAX_RETRY = timedelta(hours=1)
NAMES = {"ebay": "eBay", "etsy": "Etsy", "poshmark": "Poshmark", "mercari": "Mercari", "depop": "Depop"}


class SaleSyncError(ValueError):
    def __init__(self, detail: str, status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(UTC).isoformat()
    return value.isoformat() + "Z"


def _retry_delay(attempts: int) -> timedelta:
    return min(FIRST_RETRY * 2 ** min(max(attempts - 1, 0), 10), MAX_RETRY)


def resolve_sku(db: Session, conv: Conversation | None) -> str | None:
    """Resolve the item SKU from the current listing revision or saved notes."""
    if conv is None:
        return None
    listing = db.query(Listing).filter_by(conversation_id=conv.id).first()
    if listing and listing.current_revision_id:
        rev = db.get(ListingRevision, listing.current_revision_id)
        if rev and isinstance(rev.listing_json, dict):
            raw_sku = rev.listing_json.get("sku")
            if isinstance(raw_sku, str) and raw_sku.strip():
                return raw_sku.strip()
    notes = parse_notes(conv.notes)
    for key in ("sku", "vendooSku"):
        val = notes.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _current_revision_and_qty(db: Session, conv_id: str) -> tuple[str | None, int | None, dict[str, Any]]:
    listing = db.query(Listing).filter_by(conversation_id=conv_id).first()
    if not listing or not listing.current_revision_id:
        return None, None, {}
    rev = db.get(ListingRevision, listing.current_revision_id)
    if not rev or not isinstance(rev.listing_json, dict):
        return None, None, {}
    raw_qty = rev.listing_json.get("quantity")
    qty = raw_qty if type(raw_qty) is int else None
    return rev.id, qty, dict(rev.listing_json)


def record_sync_event(
    db: Session,
    *,
    sale_id: str,
    conversation_id: str | None,
    sku: str | None,
    sold_marketplace: str,
    target_marketplace: str,
    action: str,
    status: str,
    target_listing_id: str | None = None,
    new_listing_id: str | None = None,
    previous_quantity: int | None = None,
    new_quantity: int | None = None,
    error: str | None = None,
    next_retry_at: datetime | None = None,
    increment_attempts: bool = True,
    commit: bool = True,
) -> SaleSyncEvent:
    """Insert or update the per-marketplace sync outcome for a detected sale."""
    moment = _now()
    row = (
        db.query(SaleSyncEvent)
        .filter_by(sale_id=sale_id, target_marketplace=target_marketplace, action=action)
        .first()
    )
    if row is None:
        row = SaleSyncEvent(
            id=new_id(),
            sale_id=sale_id,
            conversation_id=conversation_id,
            sku=sku,
            sold_marketplace=sold_marketplace,
            target_marketplace=target_marketplace,
            action=action,
            target_listing_id=target_listing_id,
            new_listing_id=new_listing_id,
            previous_quantity=previous_quantity,
            new_quantity=new_quantity,
            status=status,
            attempts=1,
            error=error,
            next_retry_at=next_retry_at,
            created_at=moment,
            updated_at=moment,
        )
        db.add(row)
    else:
        if conversation_id is not None:
            row.conversation_id = conversation_id
        if sku is not None:
            row.sku = sku
        if target_listing_id is not None:
            row.target_listing_id = target_listing_id
        if new_listing_id is not None:
            row.new_listing_id = new_listing_id
        if previous_quantity is not None:
            row.previous_quantity = previous_quantity
        if new_quantity is not None:
            row.new_quantity = new_quantity
        row.status = status
        if increment_attempts:
            row.attempts = int(row.attempts or 0) + 1
        row.error = error
        row.next_retry_at = next_retry_at
        row.updated_at = moment
    if commit:
        db.commit()
    return row


def _save_quantity_revision(db: Session, conv_id: str, new_quantity: int) -> tuple[str | None, int | None]:
    rev_id, prev_qty, payload = _current_revision_and_qty(db, conv_id)
    if rev_id is None:
        return None, prev_qty
    if prev_qty == new_quantity:
        return rev_id, prev_qty
    payload["quantity"] = int(new_quantity)
    saved = listing_persistence.save_revision(
        db,
        conv_id,
        payload,
        source="sale_quantity_sync",
        parent_revision_id=rev_id,
        expected_revision_id=rev_id,
        commit=True,
    )
    return saved.id, prev_qty


def _load_adjustment_plan(db: Session, sale_id: str, remaining: int) -> dict[str, Any] | None:
    sale = db.get(MarketplaceSale, sale_id)
    if sale is None:
        return None
    if sale.order_status not in PAID_STATUSES:
        return None
    if not sale.conversation_id:
        return None
    conv = db.get(Conversation, sale.conversation_id)
    if conv is None:
        return None
    sku = resolve_sku(db, conv)
    known = listings(db, conv.id, conv=conv)
    revision_id, prev_qty = _save_quantity_revision(db, conv.id, remaining)
    targets: list[dict[str, Any]] = []
    for market in MARKETPLACES:
        entry = known.get(market) or {}
        if market == sale.marketplace:
            # Mercari caps quantity at 1; when a multi-qty item sells on Mercari and stock remains,
            # automatically relist a fresh single-quantity Mercari listing.
            if market == "mercari" and remaining > 0:
                targets.append({
                    "marketplace": "mercari",
                    "action": "mercari_relist",
                    "listing_id": entry.get("listing_id") or sale.listing_id,
                    "target_quantity": MERCARI_MAX_QUANTITY,
                })
            continue
        if entry.get("state") != "live" or not entry.get("listing_id") or entry.get("sold_here"):
            continue
        if market == "mercari":
            targets.append({
                "marketplace": "mercari",
                "action": "adjust_quantity",
                "listing_id": entry.get("listing_id"),
                "target_quantity": MERCARI_MAX_QUANTITY,
                "skip_reason": "Mercari already lists 1 unit (platform maximum).",
            })
        else:
            targets.append({
                "marketplace": market,
                "action": "adjust_quantity",
                "listing_id": entry.get("listing_id"),
                "target_quantity": remaining,
            })
    return {
        "sale_id": sale.id,
        "conversation_id": conv.id,
        "sold_marketplace": sale.marketplace,
        "sku": sku,
        "revision_id": revision_id,
        "previous_quantity": prev_qty,
        "remaining": remaining,
        "targets": targets,
    }


def _session_factory(provided=None):
    if provided is not None:
        return provided
    from anteroom.services.service_context import current

    context = current()
    if context is not None:
        return context.session_factory
    from anteroom.database import SessionLocal

    return SessionLocal


def _format_adjust_sentence(updated: list[str], relisted: list[str], failed: dict[str, str]) -> str:
    parts: list[str] = []
    if updated:
        names = ", ".join(NAMES.get(m, m) for m in updated)
        parts.append(f"Updated remaining quantity on {names}.")
    if relisted:
        names = ", ".join(NAMES.get(m, m) for m in relisted)
        parts.append(f"Relisted a fresh single-quantity listing on {names}.")
    if failed:
        names = ", ".join(NAMES.get(m, m) for m in failed)
        parts.append(f"Could not update quantity on {names}; retry from the 60-day sale tracker.")
    if not parts:
        return "Stock remains; no other live listings needed a quantity update."
    return " ".join(parts)


async def _default_updater(conv_id: str, marketplace: str, listing_id: str | None, new_qty: int,
                           sale_id: str, *, session_factory=None) -> dict[str, Any]:
    """Send only stock, pinned to the live offer verified for this paid sale."""
    from anteroom.services.automation_actions import _turn
    from anteroom.services.ebay_client import EbayClient, segment
    from anteroom.services.sale_inventory import stock_after_sale

    if marketplace != "ebay":
        return {"ok": False, "error": f"No quantity-only update is available for {NAMES.get(marketplace, marketplace)}. Adjust stock on the marketplace."}

    def binding():
        with _session_factory(session_factory)() as db:
            sale = db.get(MarketplaceSale, sale_id)
            if (not sale or sale.conversation_id != conv_id or sale.marketplace == marketplace
                    or sale.order_status not in PAID_STATUSES or not listing_id or new_qty <= 0):
                return None
            stock = stock_after_sale(db, conv_id, sale.marketplace, sale.listing_id)
            if stock["remaining"] != new_qty:
                return None
            row = db.query(MarketplaceListing).filter_by(conversation_id=conv_id, marketplace=marketplace).first()
            if (row and row.state in LIVE_STATES and not row.ebay_variant_offers and not row.ebay_group_key
                    and (row.ebay_listing_id or row.listing_id) == listing_id):
                return row.ebay_offer_id
            return None

    try:
        async with _turn(marketplace):
            offer_id = await asyncio.to_thread(binding)
            if not offer_id:
                return {"ok": False, "error": "The paid sale, remaining stock, or live eBay offer could not be verified."}
            client = await EbayClient.connect()
            offer = await client.request("GET", f"/sell/inventory/v1/offer/{segment(offer_id)}")
            if (offer.get("status") != "PUBLISHED" or not offer.get("sku")
                    or str((offer.get("listing") or {}).get("listingId")) != listing_id
                    or await asyncio.to_thread(binding) != offer_id):
                return {"ok": False, "error": "The live offer or sale changed before the quantity update."}
            reply = await client.request("POST", "/sell/inventory/v1/bulk_update_price_quantity", body={"requests": [{
                "sku": offer["sku"], "offers": [{"offerId": offer_id, "availableQuantity": new_qty}],
            }]})
            rows = reply.get("responses") or []
            if (len(rows) == 1 and str(rows[0].get("offerId")) == offer_id
                    and rows[0].get("statusCode") == 200 and not rows[0].get("errors")):
                return {"ok": True}
            return {"ok": False, "error": "eBay did not confirm the quantity update for this offer."}
    except Exception as exc:  # noqa: BLE001 - report provider failures in the sale tracker
        return {"ok": False, "error": str(exc)}


async def _default_mercari_relister(
    conv_id: str,
    revision_id: str,
    *,
    sale_id: str,
    session_factory=None,
) -> dict[str, Any]:
    from anteroom.services.automation_actions import _turn, wait_for_native_result
    from anteroom.services.native_marketplaces import ListRequest, list_now

    def eligible() -> bool:
        from anteroom.services.sale_inventory import stock_after_sale

        with _session_factory(session_factory)() as db:
            sale = db.get(MarketplaceSale, sale_id)
            if (not sale or sale.marketplace != "mercari" or sale.conversation_id != conv_id
                    or sale.order_status not in PAID_STATUSES):
                return False
            stock = stock_after_sale(db, conv_id, "mercari", sale.listing_id)
            return stock["status"] == "remaining" and (stock["remaining"] or 0) > 0

    try:
        async with _turn("mercari"):
            if not await asyncio.to_thread(eligible):
                return {"ok": False, "error": "The Mercari sale or remaining stock could not be verified."}
            res = await list_now(conv_id, "mercari", ListRequest(revision_id=revision_id, confirm=True))
            res = await wait_for_native_result(conv_id, "mercari", res, session_factory=session_factory)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    ok = res.get("state") in LIVE_STATES and not res.get("error")
    new_id_val = res.get("listing_id") or extract_listing_id("mercari", res.get("listing_url"))
    return {
        "ok": ok and bool(new_id_val),
        "listing_id": new_id_val,
        "listing_url": res.get("listing_url"),
        "error": res.get("error"),
    }


async def adjust_quantities_for_sale(
    sale_id: str,
    remaining: int,
    *,
    session_factory=None,
    updater=None,
    relister=None,
) -> dict[str, Any]:
    """Decrement remaining quantity on other live marketplaces (and relist Mercari if needed).

    Never runs for sold-out items (``remaining <= 0``) or cancelled/refunded orders.
    """
    if remaining <= 0:
        return {"sale_id": sale_id, "remaining": remaining, "events": [], "note": ""}
    factory = _session_factory(session_factory)

    def _load():
        with factory() as db:
            return _load_adjustment_plan(db, sale_id, remaining)

    plan = await asyncio.to_thread(_load)
    if not plan:
        return {"sale_id": sale_id, "remaining": remaining, "events": [], "note": ""}

    conv_id = plan["conversation_id"]
    revision_id = plan["revision_id"]
    prev_qty = plan["previous_quantity"]
    sold_market = plan["sold_marketplace"]
    sku = plan["sku"]

    updated_markets: list[str] = []
    relisted_markets: list[str] = []
    failed_markets: dict[str, str] = {}
    recorded: list[dict[str, Any]] = []

    def still_paid() -> bool:
        with factory() as db:
            sale = db.get(MarketplaceSale, sale_id)
            return bool(sale and sale.conversation_id == conv_id and sale.order_status in PAID_STATUSES)

    for target in plan["targets"]:
        if not await asyncio.to_thread(still_paid):
            break
        market = target["marketplace"]
        action = target["action"]
        listing_id = target.get("listing_id")
        target_qty = target["target_quantity"]

        if target.get("skip_reason"):
            def _rec_skip(m=market, a=action, lid=listing_id, tq=target_qty, reason=target["skip_reason"]):
                with factory() as db:
                    row = record_sync_event(
                        db,
                        sale_id=sale_id,
                        conversation_id=conv_id,
                        sku=sku,
                        sold_marketplace=sold_market,
                        target_marketplace=m,
                        action=a,
                        target_listing_id=lid,
                        previous_quantity=prev_qty,
                        new_quantity=tq,
                        status="skipped",
                        error=reason,
                        next_retry_at=None,
                        increment_attempts=False,
                    )
                    return _event_out(row, order_status="paid")

            recorded.append(await asyncio.to_thread(_rec_skip))
            continue

        if not revision_id:
            err = "Listing has no saved revision to sync quantity."
            failed_markets[market] = err

            def _rec_no_rev(m=market, a=action, lid=listing_id, tq=target_qty, e=err):
                with factory() as db:
                    row = record_sync_event(
                        db,
                        sale_id=sale_id,
                        conversation_id=conv_id,
                        sku=sku,
                        sold_marketplace=sold_market,
                        target_marketplace=m,
                        action=a,
                        target_listing_id=lid,
                        previous_quantity=prev_qty,
                        new_quantity=tq,
                        status="failed",
                        error=e,
                        next_retry_at=_now() + FIRST_RETRY,
                    )
                    return _event_out(row, order_status="paid")

            recorded.append(await asyncio.to_thread(_rec_no_rev))
            continue

        if action == "mercari_relist":
            res = (await relister(conv_id, revision_id) if relister is not None else
                   await _default_mercari_relister(conv_id, revision_id, sale_id=sale_id, session_factory=factory))
            ok = bool(res.get("ok") and res.get("listing_id"))
            new_lid = str(res["listing_id"]) if ok and res.get("listing_id") else None
            new_url = res.get("listing_url")
            err = None if ok else str(res.get("error") or "Mercari did not confirm the fresh single-quantity relist.")

            def _rec_mercari(lid=listing_id, nlid=new_lid, nurl=new_url, is_ok=ok, e=err):
                with factory() as db:
                    if is_ok and nlid:
                        auto_relist.swap_tracked_listing_id(
                            db,
                            conv_id,
                            "mercari",
                            old_listing_id=lid,
                            new_listing_id=nlid,
                            new_listing_url=nurl,
                            now=_now(),
                        )
                    row = record_sync_event(
                        db,
                        sale_id=sale_id,
                        conversation_id=conv_id,
                        sku=sku,
                        sold_marketplace=sold_market,
                        target_marketplace="mercari",
                        action="mercari_relist",
                        target_listing_id=lid,
                        new_listing_id=nlid,
                        previous_quantity=prev_qty,
                        new_quantity=MERCARI_MAX_QUANTITY,
                        status="succeeded" if is_ok else "failed",
                        error=e,
                        next_retry_at=None if is_ok else _now() + FIRST_RETRY,
                    )
                    return _event_out(row, order_status="paid")

            recorded.append(await asyncio.to_thread(_rec_mercari))
            if ok:
                relisted_markets.append("mercari")
            else:
                failed_markets["mercari"] = err or "Failed"
        else:
            res = (await updater(conv_id, market, revision_id, target_qty) if updater is not None else
                   await _default_updater(conv_id, market, listing_id, target_qty, sale_id, session_factory=factory))
            ok = bool(res.get("ok"))
            err = None if ok else str(res.get("error") or f"{NAMES.get(market, market)} did not confirm the quantity update.")

            def _rec_update(m=market, lid=listing_id, tq=target_qty, is_ok=ok, e=err):
                with factory() as db:
                    row = record_sync_event(
                        db,
                        sale_id=sale_id,
                        conversation_id=conv_id,
                        sku=sku,
                        sold_marketplace=sold_market,
                        target_marketplace=m,
                        action="adjust_quantity",
                        target_listing_id=lid,
                        previous_quantity=prev_qty,
                        new_quantity=tq,
                        status="succeeded" if is_ok else "failed",
                        error=e,
                        next_retry_at=None if is_ok else _now() + FIRST_RETRY,
                    )
                    return _event_out(row, order_status="paid")

            recorded.append(await asyncio.to_thread(_rec_update))
            if ok:
                updated_markets.append(market)
            else:
                failed_markets[market] = err or "Failed"

    note = _format_adjust_sentence(updated_markets, relisted_markets, failed_markets)
    return {
        "sale_id": sale_id,
        "remaining": remaining,
        "events": recorded,
        "note": note,
    }


def record_delist_outcomes(
    db: Session,
    sale_ids: list[str],
    conv_id: str,
    live_targets: dict[str, str | None],
    result: dict[str, dict[str, Any]],
) -> None:
    """Persist per-marketplace auto-delist outcomes into the 60-day sale tracker."""
    conv = db.get(Conversation, conv_id)
    sku = resolve_sku(db, conv)
    sales = db.query(MarketplaceSale).filter(MarketplaceSale.id.in_(sale_ids)).all()
    for sale in sales:
        for market, listing_id in live_targets.items():
            entry = result.get(market) or {}
            state = entry.get("state")
            if state == "deleted":
                status = "succeeded"
                error = None
                next_retry = None
            elif state == "failed":
                status = "failed"
                error = str(entry.get("error") or "The marketplace did not confirm the delete.")
                attempts = int(entry.get("attempts") or 1)
                next_retry = _now() + _retry_delay(attempts)
            else:
                status = "pending"
                error = entry.get("error")
                next_retry = _now() + FIRST_RETRY
            record_sync_event(
                db,
                sale_id=sale.id,
                conversation_id=conv_id,
                sku=sku,
                sold_marketplace=sale.marketplace,
                target_marketplace=market,
                action="delist",
                target_listing_id=listing_id,
                status=status,
                error=error,
                next_retry_at=next_retry,
                commit=False,
            )
    db.commit()


def _event_out(row: SaleSyncEvent, *, order_status: str) -> dict[str, Any]:
    retryable = (
        row.status in {"failed", "pending"}
        and order_status in PAID_STATUSES
        and bool(row.conversation_id)
    )
    return {
        "eventId": row.id,
        "marketplace": row.target_marketplace,
        "action": row.action,
        "status": row.status,
        "attempts": int(row.attempts or 0),
        "error": row.error,
        "listingId": row.target_listing_id,
        "newListingId": row.new_listing_id,
        "previousQuantity": row.previous_quantity,
        "newQuantity": row.new_quantity,
        "nextRetryAt": _iso(row.next_retry_at),
        "updatedAt": _iso(row.updated_at),
        "retryable": retryable,
    }


def tracker_60_days(db: Session, *, limit: int = 200) -> dict[str, Any]:
    """Unified 60-day sale detection tracker with SKU, timestamps, outcomes, retry state, and errors."""
    from anteroom.services.sale_inventory import stock_after_sale
    from anteroom.services.user_settings import auto_adjust_quantity_enabled, autodelist_enabled

    since = _now() - TRACKER_WINDOW
    sales = (
        db.query(MarketplaceSale)
        .filter(func.coalesce(MarketplaceSale.sold_at, MarketplaceSale.received_at) >= since)
        .order_by(func.coalesce(MarketplaceSale.sold_at, MarketplaceSale.received_at).desc())
        .limit(min(max(limit, 1), 500))
        .all()
    )
    sale_ids = [s.id for s in sales]
    conv_ids = {s.conversation_id for s in sales if s.conversation_id}
    convs = {
        c.id: c
        for c in db.query(Conversation).filter(Conversation.id.in_(conv_ids)).all()
    } if conv_ids else {}
    events_by_sale: dict[str, list[SaleSyncEvent]] = {}
    if sale_ids:
        for ev in (
            db.query(SaleSyncEvent)
            .filter(SaleSyncEvent.sale_id.in_(sale_ids))
            .order_by(SaleSyncEvent.updated_at.desc())
            .all()
        ):
            events_by_sale.setdefault(ev.sale_id, []).append(ev)

    sku_cache: dict[str, str | None] = {}
    listings_cache: dict[str, dict[str, Any]] = {}
    stock_cache: dict[tuple[str, str, str | None], dict[str, Any]] = {}

    entries: list[dict[str, Any]] = []
    totals = {"succeeded": 0, "failed": 0, "pending": 0, "skipped": 0}

    for sale in sales:
        conv = convs.get(sale.conversation_id) if sale.conversation_id else None
        sku: str | None = None
        if conv is not None:
            if conv.id not in sku_cache:
                sku_cache[conv.id] = resolve_sku(db, conv)
            sku = sku_cache[conv.id]
            if conv.id not in listings_cache:
                listings_cache[conv.id] = listings(db, conv.id, conv=conv)
            stock_key = (conv.id, sale.marketplace, sale.listing_id)
            if stock_key not in stock_cache:
                stock_cache[stock_key] = stock_after_sale(db, conv.id, sale.marketplace, sale.listing_id)
            stock = stock_cache[stock_key]
        else:
            stock = {"status": "unknown", "remaining": None}

        sale_events = events_by_sale.get(sale.id, [])
        outcomes = [_event_out(ev, order_status=sale.order_status) for ev in sale_events]
        seen_markets = {ev.target_marketplace for ev in sale_events}

        # Include any other live/failed marketplace listings not yet represented in SaleSyncEvent
        # so failed delists or pending items always appear in the 60-day tracker.
        if conv is not None and sale.order_status in PAID_STATUSES:
            known = listings_cache.get(conv.id, {})
            for market in MARKETPLACES:
                if market == sale.marketplace or market in seen_markets:
                    continue
                entry = known.get(market) or {}
                if not entry.get("listing_id") or entry.get("sold_here"):
                    continue
                state = entry.get("state")
                if state == "deleted":
                    outcomes.append({
                        "eventId": None,
                        "marketplace": market,
                        "action": "delist",
                        "status": "succeeded",
                        "attempts": int(entry.get("attempts") or 1),
                        "error": None,
                        "listingId": entry.get("listing_id"),
                        "newListingId": None,
                        "previousQuantity": None,
                        "newQuantity": None,
                        "nextRetryAt": None,
                        "updatedAt": entry.get("at"),
                        "retryable": False,
                    })
                elif state == "failed":
                    outcomes.append({
                        "eventId": None,
                        "marketplace": market,
                        "action": "delist",
                        "status": "failed",
                        "attempts": int(entry.get("attempts") or 1),
                        "error": entry.get("error") or "The marketplace did not confirm the delete.",
                        "listingId": entry.get("listing_id"),
                        "newListingId": None,
                        "previousQuantity": None,
                        "newQuantity": None,
                        "nextRetryAt": None,
                        "updatedAt": entry.get("at"),
                        "retryable": True,
                    })
                elif state == "live" and stock.get("status") == "sold_out":
                    outcomes.append({
                        "eventId": None,
                        "marketplace": market,
                        "action": "delist",
                        "status": "pending",
                        "attempts": 0,
                        "error": None,
                        "listingId": entry.get("listing_id"),
                        "newListingId": None,
                        "previousQuantity": None,
                        "newQuantity": None,
                        "nextRetryAt": None,
                        "updatedAt": entry.get("at"),
                        "retryable": True,
                    })

        if not sku and sale_events:
            sku = next((ev.sku for ev in sale_events if ev.sku), None)

        for outcome in outcomes:
            st = outcome["status"]
            if st in totals:
                totals[st] += 1

        entries.append({
            "saleId": sale.id,
            "conversationId": sale.conversation_id,
            "title": conv.title if conv else None,
            "sku": sku,
            "soldOn": sale.marketplace,
            "orderId": sale.order_id,
            "orderStatus": sale.order_status,
            "source": sale.source,
            "quantity": sale.quantity,
            "stockStatus": stock["status"],
            "remainingQuantity": stock["remaining"],
            "soldAt": _iso(sale.sold_at or sale.received_at),
            "receivedAt": _iso(sale.received_at),
            "outcomes": outcomes,
        })

    return {
        "windowDays": TRACKER_WINDOW.days,
        "autodelist": autodelist_enabled(),
        "autoAdjustQuantity": auto_adjust_quantity_enabled(),
        "summary": {
            "totalSales": len(entries),
            **totals,
        },
        "entries": entries,
    }


async def retry_sync_event(
    event_id: str,
    *,
    session_factory=None,
    delister=None,
    updater=None,
    relister=None,
) -> dict[str, Any]:
    """One-click retry for a failed or pending delist / quantity-adjust / Mercari-relist event."""
    from anteroom.services.sale_inventory import stock_after_sale

    factory = _session_factory(session_factory)

    def _load():
        with factory() as db:
            row = db.get(SaleSyncEvent, event_id)
            if row is None:
                raise SaleSyncError("Sale sync event not found.", status_code=404)
            sale = db.get(MarketplaceSale, row.sale_id)
            if sale is None:
                raise SaleSyncError("Underlying sale record not found.", status_code=404)
            if sale.order_status not in PAID_STATUSES:
                raise SaleSyncError(
                    "Cannot retry because this order is cancelled, refunded, or awaiting cancellation review.",
                    status_code=409,
                )
            if not row.conversation_id:
                raise SaleSyncError("This sale is not linked to an Anteroom listing.", status_code=409)
            stock = stock_after_sale(db, row.conversation_id, sale.marketplace, sale.listing_id)
            if row.action == "delist" and stock["status"] != "sold_out":
                raise SaleSyncError(
                    "Stock remains or could not be verified. Cannot delist while inventory remains.",
                    status_code=409,
                )
            rev_id, _qty, _payload = _current_revision_and_qty(db, row.conversation_id)
            return {
                "event_id": row.id,
                "sale_id": row.sale_id,
                "conversation_id": row.conversation_id,
                "sku": row.sku,
                "sold_marketplace": row.sold_marketplace,
                "target_marketplace": row.target_marketplace,
                "action": row.action,
                "target_listing_id": row.target_listing_id,
                "previous_quantity": row.previous_quantity,
                "new_quantity": row.new_quantity or stock.get("remaining") or 1,
                "attempts": int(row.attempts or 0),
                "order_status": sale.order_status,
                "revision_id": rev_id,
            }

    info = await asyncio.to_thread(_load)
    action = info["action"]
    conv_id = info["conversation_id"]
    market = info["target_marketplace"]
    new_listing_id: str | None = None
    new_listing_url: str | None = None
    ok = False
    error: str | None = None

    if action == "delist":
        try:
            if delister is not None:
                res = await delister(conv_id, market)
                ok = bool(res.get("ok"))
                error = None if ok else str(res.get("error") or "The marketplace did not confirm the delete.")
            else:
                res = await delist(conv_id, [market], confirm=True, sale_id=info["sale_id"])
                entry = res.get(market) or {}
                ok = entry.get("state") == "deleted"
                error = None if ok else str(entry.get("error") or "The marketplace did not confirm the delete.")
        except DelistError as exc:
            ok = False
            error = str(exc)
    elif action == "mercari_relist":
        if not info["revision_id"]:
            error = "Listing has no saved revision to relist."
        else:
            res = (await relister(conv_id, info["revision_id"]) if relister is not None else
                   await _default_mercari_relister(conv_id, info["revision_id"], sale_id=info["sale_id"], session_factory=factory))
            ok = bool(res.get("ok") and res.get("listing_id"))
            new_listing_id = str(res["listing_id"]) if ok and res.get("listing_id") else None
            new_listing_url = res.get("listing_url")
            error = None if ok else str(res.get("error") or "Mercari did not confirm the fresh single-quantity relist.")
    elif action == "adjust_quantity":
        if not info["revision_id"]:
            error = "Listing has no saved revision to update."
        else:
            qty = int(info["new_quantity"])
            res = (await updater(conv_id, market, info["revision_id"], qty) if updater is not None else
                   await _default_updater(conv_id, market, info["target_listing_id"], qty, info["sale_id"],
                                          session_factory=factory))
            ok = bool(res.get("ok"))
            error = None if ok else str(res.get("error") or f"{NAMES.get(market, market)} did not confirm the quantity update.")
    else:
        raise SaleSyncError(f"Unsupported sync action: {action}")

    next_attempts = info["attempts"] + 1
    next_retry = None if ok else _now() + _retry_delay(next_attempts)

    def _save():
        with factory() as db:
            if action == "mercari_relist" and ok and new_listing_id:
                auto_relist.swap_tracked_listing_id(
                    db,
                    conv_id,
                    "mercari",
                    old_listing_id=info["target_listing_id"],
                    new_listing_id=new_listing_id,
                    new_listing_url=new_listing_url,
                    now=_now(),
                )
            row = record_sync_event(
                db,
                sale_id=info["sale_id"],
                conversation_id=conv_id,
                sku=info["sku"],
                sold_marketplace=info["sold_marketplace"],
                target_marketplace=market,
                action=action,
                target_listing_id=info["target_listing_id"],
                new_listing_id=new_listing_id,
                previous_quantity=info["previous_quantity"],
                new_quantity=info["new_quantity"] if action != "delist" else None,
                status="succeeded" if ok else "failed",
                error=error,
                next_retry_at=next_retry,
                increment_attempts=True,
            )
            return _event_out(row, order_status=info["order_status"])

    return await asyncio.to_thread(_save)
