"""Sale amounts for eBay and Etsy orders, from each marketplace's own order records.

Depop, Poshmark and Mercari amounts arrive with their Chrome order history
(``order_history``). eBay and Etsy orders are recorded by ``order_recovery``;
this adds their money from the same read-only APIs:

- eBay: the Fulfillment order (sale price after discounts, buyer shipping,
  eBay's fees, refunds) and the Finances API's shipping-label charges.
- Etsy: the receipt (sale price after coupons, buyer shipping, refunds). Etsy
  does not report its own fees or label purchases per order, so those stay for
  the seller to record.

Amounts the seller entered are never overwritten, and item cost is always the
seller's. A value the marketplace does not report keeps what was recorded.
Nothing on any marketplace changes.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.services.order_history import TOTALS, Line, Order, line_amounts

log = logging.getLogger(__name__)

MARKETPLACES = ("ebay", "etsy")
# What each marketplace reports per order. A sale missing one of these is read again.
REPORTED = {"ebay": ("item_revenue", "shipping_credit", "refunds", "fees"),
            "etsy": ("item_revenue", "shipping_credit", "refunds")}
# Statuses whose money is worth recording: the sale happened, maybe refunded since.
MONEY_STATUSES = ("paid", "partially_refunded", "refunded")
# An order is read for its amounts at most this often per process.
RECHECK_S = 6 * 3600
# Older sales recorded before amounts were read, filled a few orders per catch-up.
BACKFILL_ORDERS = 25
_checked: dict[tuple[str, str, str], float] = {}


@dataclass(frozen=True)
class OrderMoney:
    currency: str
    totals: dict[str, Decimal | None]
    # Each line's own sale price, and its share weight, by line ID.
    line_revenue: dict[str, Decimal | None]
    weights: dict[str, Decimal | None]


def _decimal(value) -> Decimal | None:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return amount if amount.is_finite() else None


def _ebay_amount(value, *, signed: bool = False) -> Decimal | None:
    if not isinstance(value, dict) or value.get("currency") != "USD":
        return None
    amount = _decimal(value.get("value"))
    if amount is None or (amount < 0 and not signed):
        return None
    return amount


def _etsy_amount(value) -> Decimal | None:
    if not isinstance(value, dict) or value.get("currency_code") != "USD":
        return None
    amount, divisor = value.get("amount"), value.get("divisor")
    if type(amount) is not int or type(divisor) is not int or divisor <= 0 or amount < 0:
        return None
    return Decimal(amount) / Decimal(divisor)


def ebay_money(detail: dict, lines: list[tuple[str, str | None]], labels: Decimal | None) -> OrderMoney | None:
    """Amounts from a Fulfillment order. ``lines`` are (Trading line ID, item ID); ``labels`` is the label spend."""
    pricing = detail.get("pricingSummary")
    if not isinstance(pricing, dict):
        return None
    subtotal = _ebay_amount(pricing.get("priceSubtotal"))
    if subtotal is None:
        return None
    # Discounts are negative; absent means none.
    discount = _ebay_amount(pricing.get("priceDiscount"), signed=True) or Decimal(0)
    delivery = _ebay_amount(pricing.get("deliveryCost"))
    delivery_discount = _ebay_amount(pricing.get("deliveryDiscount"), signed=True) or Decimal(0)
    summary = detail.get("paymentSummary")
    refund_rows = summary.get("refunds") if isinstance(summary, dict) else None
    refunds: Decimal | None = None
    if isinstance(refund_rows, list):
        refunds = Decimal(0)
        for row in refund_rows:
            if not isinstance(row, dict):
                refunds = None
                break
            if row.get("refundStatus") != "REFUNDED":
                continue
            amount = _ebay_amount(row.get("amount"))
            if amount is None:
                refunds = None
                break
            refunds += amount
    # Fulfillment line items carry REST IDs; the item ID ties them to Trading's lines.
    costs: dict[str, list[Decimal | None]] = {}
    for item in detail.get("lineItems") or []:
        if isinstance(item, dict) and item.get("legacyItemId"):
            cost = _ebay_amount(item.get("discountedLineItemCost")) or _ebay_amount(item.get("lineItemCost"))
            costs.setdefault(str(item["legacyItemId"]), []).append(cost)
    revenue = {line_id: (costs[item][0] if item in costs and len(costs[item]) == 1 else None) for line_id, item in lines}
    return OrderMoney("USD", {
        "item_revenue": max(subtotal + discount, Decimal(0)),
        "shipping_credit": max(delivery + delivery_discount, Decimal(0)) if delivery is not None else None,
        "refunds": refunds,
        "fees": _ebay_amount(detail.get("totalMarketplaceFee")),
        "shipping_cost": labels,
    }, revenue, dict(revenue))


def etsy_money(receipt: dict, lines: list[tuple[str, str | None]]) -> OrderMoney | None:
    """Amounts from an Etsy receipt. Etsy's fees and label purchases are not on it."""
    subtotal = _etsy_amount(receipt.get("subtotal"))
    if subtotal is None:
        return None
    refund_rows = receipt.get("refunds")
    refunds: Decimal | None = None
    if isinstance(refund_rows, list):
        amounts = [_etsy_amount(row.get("amount")) if isinstance(row, dict) else None for row in refund_rows]
        refunds = sum(amounts, Decimal(0)) if all(amount is not None for amount in amounts) else None  # type: ignore[arg-type]
    discounted = _etsy_amount(receipt.get("discount_amt")) not in (None, Decimal(0))
    prices: dict[str, Decimal | None] = {}
    for row in receipt.get("transactions") or []:
        if not isinstance(row, dict):
            continue
        price, quantity = _etsy_amount(row.get("price")), row.get("quantity")
        prices[str(row.get("transaction_id"))] = price * quantity if price is not None and type(quantity) is int and quantity > 0 else None
    weights = {line_id: prices.get(line_id) for line_id, _ in lines}
    # A coupon lowers the receipt, not each line: share it by price instead of using list prices.
    revenue = {line_id: None if discounted else weight for line_id, weight in weights.items()}
    return OrderMoney("USD", {
        "item_revenue": subtotal,
        "shipping_credit": _etsy_amount(receipt.get("total_shipping_cost")),
        "refunds": refunds,
        "fees": None,
        "shipping_cost": None,
    }, revenue, weights)


def _text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def needs_amounts(marketplace: str, rows: list[MarketplaceSale]) -> bool:
    """True when an order line the marketplace reported lacks amounts it can supply, or they no longer match the order."""
    from vendoo_studio.services.sales_ledger import order_basis

    for row in rows:
        if row.source != "marketplace" or row.order_status not in MONEY_STATUSES:
            continue
        raw = row.financials
        if not isinstance(raw, dict):
            return True
        if raw.get("source") == "seller":
            continue
        if raw.get("source") != "marketplace" or raw.get("order_basis") != order_basis(row) \
                or any(raw.get(field) is None for field in REPORTED[marketplace]):
            return True
    return False


def store(db: Session, marketplace: str, order_id: str, money: OrderMoney, observed: datetime) -> int:
    """Write the order's amounts onto its lines the seller has not edited. Returns the lines changed."""
    from vendoo_studio.services.api_job_lock import start_gate
    from vendoo_studio.services.sales_ledger import order_basis

    with start_gate():
        rows = [row for row in db.query(MarketplaceSale).filter_by(marketplace=marketplace, order_id=order_id, source="marketplace")
                .order_by(MarketplaceSale.line_id).all() if row.line_id in money.weights]
        if not rows:
            return 0
        order = Order(order_id, "paid", False, None, money.currency, money.totals,
                      tuple(Line(row.line_id, row.listing_id or "", (), money.weights[row.line_id], money.line_revenue[row.line_id])
                            for row in rows))
        changed = 0
        for row, amounts in zip(rows, line_amounts(order), strict=True):
            raw = row.financials if isinstance(row.financials, dict) else {}
            if raw.get("source") == "seller" or row.order_status not in MONEY_STATUSES:
                continue
            values = {field: _text(amounts[field]) if amounts[field] is not None
                      else (raw.get(field) if isinstance(raw.get(field), str) else None) for field in TOTALS}
            cost = raw.get("item_cost") if isinstance(raw.get("item_cost"), str) else None
            basis = order_basis(row)
            if raw.get("source") == "marketplace" and raw.get("order_basis") == basis and raw.get("currency") == money.currency \
                    and all(raw.get(field) == value for field, value in values.items()):
                continue
            row.financials = {"currency": money.currency, **values, "item_cost": cost, "order_basis": basis,
                              "updated_at": observed.replace(tzinfo=UTC).isoformat(), "source": "marketplace"}
            row.financial_revision = (row.financial_revision or 0) + 1
            changed += 1
        db.commit()
    return changed


def _seller() -> str:
    from vendoo_studio.services.service_context import current

    context = current()
    return context.seller_id if context else ""


def _due(marketplace: str, order_id: str) -> bool:
    key = (_seller(), marketplace, order_id)
    now = time.monotonic()
    if len(_checked) > 50_000:
        for stale in [item for item, at in _checked.items() if now >= at + RECHECK_S]:
            del _checked[stale]
    if now < _checked.get(key, float("-inf")) + RECHECK_S:
        return False
    _checked[key] = now
    return True


async def _ebay_labels(client, order_id: str) -> Decimal | None:
    from vendoo_studio.services.ebay_finances import order_financials

    try:
        financials = await order_financials(client, order_id)
    except Exception:  # noqa: BLE001 - labels are optional; the other amounts still count
        log.warning("eBay label charges could not be read for one order")
        return None
    return financials.shipping_labels if financials else None


async def _ebay_detail(client, order_id: str) -> dict | None:
    detail = await client.request("GET", f"/sell/fulfillment/v1/order/{order_id}")
    return detail if isinstance(detail, dict) and detail.get("orderId") == order_id else None


async def record(session_factory: Callable[[], Session], marketplace: str, orders: list[tuple[str, dict | None]],
                 observed: datetime, client=None) -> int:
    """Record amounts for orders just read, using the order record already fetched with them.

    ``orders`` are (order ID, Fulfillment order or Etsy receipt). eBay label
    charges are one more read per order, made only when amounts are missing.
    Each database step runs on a worker thread with its own short session, so
    no connection is held while eBay answers.
    """
    changed = 0
    for order_id, source in orders:
        lines = await asyncio.to_thread(_lines_needing_amounts, session_factory, marketplace, order_id, bool(source))
        if lines is None or not source:
            continue
        try:
            if marketplace == "ebay":
                money = ebay_money(source, lines, None)
                if money is not None and client is not None:
                    money = replace(money, totals={**money.totals, "shipping_cost": await _ebay_labels(client, order_id)})
            else:
                money = etsy_money(source, lines)
            if money is not None:
                changed += await asyncio.to_thread(_store, session_factory, marketplace, order_id, money, observed)
        except Exception:  # noqa: BLE001 - one unreadable order must not stop the others
            log.warning("%s amounts could not be recorded for one order", marketplace)
    return changed


def _lines_needing_amounts(session_factory, marketplace: str, order_id: str, readable: bool) -> list[tuple[str, str | None]] | None:
    with session_factory() as db:
        rows = db.query(MarketplaceSale).filter_by(marketplace=marketplace, order_id=order_id).all()
        # Marked checked even when unreadable, so a bad order is not read again every catch-up.
        if not needs_amounts(marketplace, rows) or not _due(marketplace, order_id) or not readable:
            return None
        return [(row.line_id, row.listing_id) for row in rows if row.source == "marketplace"]


def _store(session_factory, marketplace: str, order_id: str, money: OrderMoney, observed: datetime) -> int:
    with session_factory() as db:
        try:
            return store(db, marketplace, order_id, money, observed)
        except Exception:
            db.rollback()
            raise


def _read_backlog(session_factory, marketplace: str, limit: int) -> list[str]:
    with session_factory() as db:
        return _backlog(db, marketplace, limit)


def _backlog(db: Session, marketplace: str, limit: int) -> list[str]:
    rows = db.query(MarketplaceSale).filter(
        MarketplaceSale.marketplace == marketplace, MarketplaceSale.source == "marketplace",
        MarketplaceSale.order_status.in_(MONEY_STATUSES), MarketplaceSale.order_id != MarketplaceSale.line_id).all()
    grouped: dict[str, list[MarketplaceSale]] = {}
    for row in rows:
        grouped.setdefault(row.order_id, []).append(row)
    newest = sorted(grouped.items(), key=lambda item: max((row.sold_at or row.received_at) for row in item[1]), reverse=True)
    key = _seller()
    now = time.monotonic()
    return [order_id for order_id, lines in newest
            if needs_amounts(marketplace, lines) and now >= _checked.get((key, marketplace, order_id), float("-inf")) + RECHECK_S][:limit]


async def backfill(marketplace: str, session_factory: Callable[[], Session], limit: int = BACKFILL_ORDERS) -> int:
    """Fill amounts for older recorded sales, newest first, a few orders at a time."""
    order_ids = await asyncio.to_thread(_read_backlog, session_factory, marketplace, limit)
    if not order_ids:
        return 0
    from vendoo_studio.services.ebay_client import EbayClient
    from vendoo_studio.services.etsy_workspace import EtsyClient

    ebay = await EbayClient.connect() if marketplace == "ebay" else None
    etsy = await EtsyClient.connect() if marketplace == "etsy" else None
    observed = datetime.now(UTC).replace(tzinfo=None)
    sources: list[tuple[str, dict | None]] = []
    for order_id in order_ids:
        try:
            if ebay is not None:
                source = await _ebay_detail(ebay, order_id)
            else:
                assert etsy is not None
                receipt = await etsy.read(f"/receipts/{order_id}")
                source = receipt if str(receipt.get("receipt_id")) == order_id else None
        except Exception:  # noqa: BLE001 - try the next order; this one waits for the next check
            log.warning("%s order could not be read for its amounts", marketplace)
            _checked[(_seller(), marketplace, order_id)] = time.monotonic()
            continue
        sources.append((order_id, source))
    return await record(session_factory, marketplace, sources, observed, ebay)
