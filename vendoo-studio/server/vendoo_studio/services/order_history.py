"""Sales from each Chrome marketplace's own order history, with its amounts.

On Depop, Poshmark and Mercari, Anteroom records a sale only
from an order the marketplace reports: its ID, its lines and its money. A
listing that merely reads sold is not a sale: sellers mark listings sold for
sales made elsewhere.

Each read stores new orders and updates open ones. An order the marketplace
reports for a listing replaces what stood in for it before:

- a listing-only report from the old sold check (order, line and listing all
  the listing's ID);
- the seller's Mark as sold on that marketplace for that item; and
- the Vendoo sale kept for that item on that marketplace.

The item cost the seller recorded on any of them is kept. Amounts the seller
edited on a line are never overwritten. Nothing on any marketplace changes.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace

from sqlalchemy.orm import Session

from vendoo_studio.models.marketplace_sale import ORDER_STATUSES, MarketplaceSale

log = logging.getLogger("vendoo_studio.order_history")

HISTORY_MARKETS = ("depop", "poshmark", "mercari")
CAPABILITY = "marketplace.sales_history"
# Totals Anteroom keeps per line; item cost is the seller's, never the marketplace's.
TOTALS = ("item_revenue", "shipping_credit", "refunds", "fees", "shipping_cost")
# A sale this recent is new to the seller: it is announced, and may delist the
# item elsewhere when they turned that on. Older history is recorded quietly.
ANNOUNCE_WITHIN = timedelta(days=3)
# A first read can walk a shop's whole history, one order page at a time.
READ_TIMEOUT_S = 20 * 60
MAX_ORDERS = 2000
MAX_LINES = 50
CENT = Decimal("0.01")

_ORDER_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_LISTING_ID = {
    "poshmark": re.compile(r"^[a-f0-9]{24}$"),
    "mercari": re.compile(r"^m\d{6,}$"),
    "depop": re.compile(r"^[a-z0-9][a-z0-9-]{0,200}$"),
}


class HistoryError(ValueError):
    pass


@dataclass(frozen=True)
class Line:
    line_id: str
    listing_id: str
    alt_ids: tuple[str, ...]
    weight: Decimal | None
    item_revenue: Decimal | None


@dataclass(frozen=True)
class Order:
    order_id: str
    status: str
    final: bool
    sold_at: datetime | None
    currency: str
    totals: dict[str, Decimal | None]
    lines: tuple[Line, ...]


def _money(value) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"-?\d{1,9}(\.\d{1,4})?", value):
        raise HistoryError("unreadable amount")
    return Decimal(value)


def _moment(value) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise HistoryError("unreadable date") from exc
    return parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo else parsed


def parse_orders(marketplace: str, raw: object) -> list[Order]:
    """The orders the extension sent, checked field by field. Anything malformed rejects the whole read."""
    if marketplace not in _LISTING_ID or not isinstance(raw, dict) or not isinstance(raw.get("orders"), list):
        raise HistoryError("unreadable orders")
    if len(raw["orders"]) > MAX_ORDERS:
        raise HistoryError("too many orders")
    pattern = _LISTING_ID[marketplace]
    orders: dict[str, Order] = {}
    for item in raw["orders"]:
        if not isinstance(item, dict):
            raise HistoryError("unreadable order")
        order_id = str(item.get("order_id") or "")
        status = item.get("status")
        totals = item.get("totals")
        lines = item.get("lines")
        if (not _ORDER_ID.fullmatch(order_id) or status not in ORDER_STATUSES or not isinstance(totals, dict)
                or not isinstance(lines, list) or not 0 < len(lines) <= MAX_LINES or item.get("currency") != "USD"):
            raise HistoryError("unreadable order")
        parsed_lines = []
        for line in lines:
            if not isinstance(line, dict):
                raise HistoryError("unreadable line")
            line_id = str(line.get("line_id") or "")
            listing_id = str(line.get("listing_id") or "")
            alt_ids = line.get("alt_ids") or []
            if (not _ORDER_ID.fullmatch(line_id) and not pattern.fullmatch(line_id)) or not pattern.fullmatch(listing_id) \
                    or not isinstance(alt_ids, list) or not all(isinstance(alt, str) and _ORDER_ID.fullmatch(alt) for alt in alt_ids):
                raise HistoryError("unreadable line")
            parsed_lines.append(Line(line_id, listing_id, tuple(alt_ids[:5]), _money(line.get("weight")), _money(line.get("item_revenue"))))
        if len({line.line_id for line in parsed_lines}) != len(parsed_lines):
            raise HistoryError("duplicate lines")
        orders[order_id] = Order(order_id, status, item.get("final") is True, _moment(item.get("sold_at")), "USD",
                                 {field: _money(totals.get(field)) for field in TOTALS}, tuple(parsed_lines))
    return list(orders.values())


def _split(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """Share ``total`` across lines by weight, to the cent, adding up exactly."""
    if len(weights) == 1:
        return [total]
    positive = [weight if weight and weight > 0 else Decimal(0) for weight in weights]
    base = sum(positive) or Decimal(len(weights))
    shares = [(weight if sum(positive) else Decimal(1)) / base * total for weight in positive]
    rounded = [share.quantize(CENT, rounding=ROUND_HALF_UP) for share in shares]
    rounded[-1] += total - sum(rounded)
    return rounded


def line_amounts(order: Order) -> list[dict[str, Decimal | None]]:
    """Each line's share of the order's totals. A line's own sale price is used when every line has one."""
    weights = [line.weight or Decimal(0) for line in order.lines]
    amounts: list[dict[str, Decimal | None]] = [{} for _ in order.lines]
    for field in TOTALS:
        total = order.totals[field]
        if field == "item_revenue" and all(line.item_revenue is not None for line in order.lines):
            shares: list[Decimal | None] = [line.item_revenue for line in order.lines]
        elif total is None:
            shares = [None] * len(order.lines)
        else:
            shares = list(_split(total, weights))
        for row, share in zip(amounts, shares, strict=True):
            row[field] = share
    return amounts


def settled_orders(db: Session, marketplace: str) -> list[str]:
    """Orders read before that the marketplace said would not change, so their detail need not be read again."""
    settled: dict[str, bool] = {}
    for order_id, financials in db.query(MarketplaceSale.order_id, MarketplaceSale.financials).filter(
            MarketplaceSale.marketplace == marketplace, MarketplaceSale.source == "marketplace").all():
        final = isinstance(financials, dict) and financials.get("order_final") is True
        settled[order_id] = settled.get(order_id, True) and final
    return [order_id for order_id, final in settled.items() if final]


def _placeholders(db: Session, marketplace: str, listing_ids: list[str]) -> list[MarketplaceSale]:
    """Listing-only reports of the old sold check for these listings."""
    return [row for row in db.query(MarketplaceSale).filter(
        MarketplaceSale.marketplace == marketplace, MarketplaceSale.listing_id.in_(listing_ids)).all()
        if row.source == "marketplace" and row.order_id == row.line_id == row.listing_id]


def save_orders(db: Session, marketplace: str, orders: list[Order], observed: datetime) -> dict:
    """Store each order line once, replacing what stood in for it. Returns counts and the sales to announce."""
    from vendoo_studio.services.relay_sales import find_listing
    from vendoo_studio.services.sale_matching import carry_over, replace_stand_in
    from vendoo_studio.services.sales_ledger import order_basis

    counts = {"recorded": 0, "updated": 0, "replaced": 0}
    announce: list[str] = []
    for order in orders:
        for line, money in zip(order.lines, line_amounts(order), strict=True):
            keys = [line.line_id, *line.alt_ids, line.listing_id]
            sale = (db.query(MarketplaceSale).filter_by(marketplace=marketplace, order_id=order.order_id, line_id=line.line_id).first()
                    or db.query(MarketplaceSale).filter(MarketplaceSale.marketplace == marketplace,
                                                         MarketplaceSale.order_id == order.order_id,
                                                         MarketplaceSale.line_id.in_(keys)).first())
            placeholders = [row for row in _placeholders(db, marketplace, [line.listing_id, *line.alt_ids]) if row is not sale]
            owner = find_listing(db, marketplace, line.listing_id)
            conversation_id = (sale.conversation_id if sale else None) or (owner.id if owner else None) \
                or next((row.conversation_id for row in placeholders if row.conversation_id), None)
            for row in placeholders:
                db.delete(row)
            db.flush()
            fresh = sale is None
            if sale is None:
                recent = order.sold_at is not None and observed - order.sold_at <= ANNOUNCE_WITHIN
                sale = MarketplaceSale(marketplace=marketplace, order_id=order.order_id, line_id=line.line_id,
                                       source="marketplace", state="new" if recent and order.status == "paid" else "dismissed",
                                       received_at=observed, resolved_at=None if recent else observed)
                db.add(sale)
            sale.line_id = line.line_id
            sale.listing_id = line.listing_id
            sale.quantity = 1
            sale.order_status = order.status
            sale.order_observed_at = observed
            sale.conversation_id = conversation_id
            raw = sale.financials if isinstance(sale.financials, dict) else {}
            if order.sold_at is not None and "sold_on" not in raw:
                sale.sold_at = order.sold_at
            db.flush()
            for row in placeholders:
                carry_over(sale, row)
            replaced = len(placeholders) + (fresh and replace_stand_in(db, sale) is not None)
            counts["replaced"] += replaced
            raw = sale.financials if isinstance(sale.financials, dict) else {}
            if raw.get("source") != "seller":
                sale.financials = {"currency": order.currency,
                                   **{field: format(value, "f") if value is not None else None for field, value in money.items()},
                                   "item_cost": raw.get("item_cost"), "order_basis": order_basis(sale),
                                   "updated_at": observed.replace(tzinfo=UTC).isoformat(),
                                   "source": "marketplace", "order_final": order.final}
                sale.financial_revision = (sale.financial_revision or 0) + 1
            counts["recorded" if fresh else "updated"] += 1
            if fresh and sale.state == "new":
                announce.append(sale.id)
    db.commit()
    return {**counts, "announce": announce}


def is_listing_report(sale: MarketplaceSale) -> bool:
    """A listing the old sold check read as sold: no order, and amounts the seller never confirmed."""
    raw = sale.financials if isinstance(sale.financials, dict) else {}
    return (sale.source == "marketplace" and sale.order_id == sale.line_id == sale.listing_id
            and raw.get("source") != "seller")


def clear_listing_reports(db: Session, marketplace: str) -> int:
    """Remove the old sold check's listing-only reports once the whole order history has been read.

    Every real sale on this marketplace now has its order, so a report with no
    order behind it was a listing that only looked sold.
    """
    rows = [row for row in db.query(MarketplaceSale).filter(
        MarketplaceSale.marketplace == marketplace, MarketplaceSale.order_id == MarketplaceSale.line_id).all()
        if is_listing_report(row)]
    for row in rows:
        db.delete(row)
    db.commit()
    return len(rows)


def bridge_can_read() -> bool:
    from vendoo_studio.services.extension_connection import current_extension

    extension_manager = current_extension()
    return extension_manager.connected and CAPABILITY in extension_manager.capabilities


async def sync(marketplace: str, *, session_factory=None) -> dict:
    """Read this marketplace's orders through Chrome and store them. Nothing on the marketplace changes."""
    from vendoo_studio.services import browser_bridge
    from vendoo_studio.services.api_job_lock import start_gate
    from vendoo_studio.services.hosted_browser_relay import ChromeReadError
    from vendoo_studio.services.relay_sales import reconcile_unmatched

    if session_factory is None:
        from vendoo_studio.database import SessionLocal

        session_factory = SessionLocal
    if marketplace not in HISTORY_MARKETS:
        return {"ok": False, "error": "Sales are not read from this marketplace."}
    if not bridge_can_read():
        return {"ok": False, "error": "Update the Anteroom Chrome extension to read sales from this marketplace."}
    def read_settled() -> list:
        with session_factory() as db:
            return settled_orders(db, marketplace)

    settled = await asyncio.to_thread(read_settled)
    try:
        from vendoo_studio.services.service_context import current
        from vendoo_studio.services.hosted_browser_relay import relay_for

        context = current()
        payload = {"marketplace": marketplace, "settled": settled}
        if context is not None:
            raw = await relay_for(context.seller_id).read("marketplace.sales_history", payload, READ_TIMEOUT_S)
        else:
            raw = await browser_bridge.request(SimpleNamespace(id=None), "marketplace.sales_history",
                                               payload, timeout=READ_TIMEOUT_S)
    except (browser_bridge.BrowserBridgeError, ChromeReadError) as exc:
        return {"ok": False, "error": str(exc)}
    if not isinstance(raw, dict) or raw.get("skipped") is True:
        return {"ok": False, "skipped": True}
    if raw.get("ok") is not True:
        messages = {
            "signed_out": "Sign in to this marketplace in Chrome.",
            "challenge": "Open this marketplace in Chrome and finish its browser check, then try again.",
            "rate_limited": "The marketplace is limiting sales reads. Wait a few minutes, then try again.",
            "network": "The marketplace did not answer the sales read. Try again shortly.",
            "schema_drift": "The marketplace returned sales Anteroom can't read. Saved records were kept.",
            "rejected": "The marketplace refused the sales read. Saved records were kept.",
        }
        code = raw.get("error_code") or raw.get("error")
        return {"ok": False, "error": messages.get(code, "Couldn't read sales.") if isinstance(code, str) else "Couldn't read sales."}
    try:
        orders = parse_orders(marketplace, raw)
    except HistoryError:
        log.warning("%s returned orders Anteroom could not read", marketplace)
        return {"ok": False, "error": "The marketplace returned sales Anteroom can't read. Nothing was saved."}
    unreadable = raw.get("unreadable") if type(raw.get("unreadable")) is int else 0
    more = raw.get("more") is True
    observed = datetime.now(UTC).replace(tzinfo=None)

    def store() -> dict:
        from vendoo_studio.services.vendoo_sales_archive import ensure_archived

        with session_factory() as db:
            with start_gate():
                ensure_archived(db)
                result = save_orders(db, marketplace, orders, observed)
                if not more and not unreadable:
                    result["cleared"] = clear_listing_reports(db, marketplace)
            result["matched"] = reconcile_unmatched(db, marketplace=marketplace)
            return result

    result = await asyncio.to_thread(store)
    announce = result.pop("announce")
    if announce:
        from vendoo_studio.services.marketplace_accounts import announce_sales

        await announce_sales(announce, session_factory=session_factory)
    log.info("%s sales read: %s", marketplace, result)
    return {"ok": True, "orders": len(orders), "more": more, "unreadable": unreadable, **result}
