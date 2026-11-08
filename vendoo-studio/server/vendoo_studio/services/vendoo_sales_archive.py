"""Keep the sales Vendoo imported, once, as Anteroom's own records.

Vendoo's sale notes (``vendooSale``, ``vendooDates``) used to be read every
time sales were shown. They are now copied once into the sales ledger, and
from then on only the ledger is read. The notes themselves stay as they are.

Each item's Vendoo sale becomes one of:

- the amounts of the ledger line for the same order, when that line has none
  of its own (the line on Vendoo's marketplace, as before); or
- a line of its own with ``source="vendoo"``, when the ledger has no line for
  it, or only a listing the old sold check read as sold, which it replaces.

A sale later read from the marketplace itself replaces the Vendoo copy for
that item and marketplace (see ``sale_matching``).
"""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from vendoo_studio.models.conversation import Conversation, utcnow
from vendoo_studio.models.marketplace_sale import PAID_STATUSES, MarketplaceSale

log = logging.getLogger("vendoo_studio.vendoo_sales_archive")

ARCHIVED_KEY = "vendoo_sales_archived_at"
ORDER_ID = "Vendoo import"
_archive_lock = threading.Lock()


def _amount(value) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    return format(amount, "f") if amount.is_finite() else None


def archived() -> bool:
    from vendoo_studio.services.user_settings import read_settings

    return bool(read_settings().get(ARCHIVED_KEY))


def ensure_archived(db: Session) -> None:
    """Copy Vendoo's sales into the ledger the first time; afterwards do nothing."""
    from vendoo_studio.services.user_settings import update_settings

    with _archive_lock:
        if archived():
            return
        count = archive(db)

        def mark(payload: dict) -> None:
            payload[ARCHIVED_KEY] = datetime.now(UTC).isoformat()

        update_settings(mark)
    log.info("kept %s Vendoo sales as Anteroom records", count)


def archive(db: Session) -> int:
    """Write every item's Vendoo sale into the ledger. Safe to run again: a kept sale is matched, not copied."""
    from vendoo_studio.services.inventory_analytics import _marketplace
    from vendoo_studio.services.order_history import is_listing_report
    from vendoo_studio.services.sale_matching import matching_sale_index
    from vendoo_studio.services.sales_ledger import order_basis
    from vendoo_studio.services.sell_through import _parse_moment
    from vendoo_studio.services.vendoo_import import VENDOO_MARKETPLACE_ALIASES, parse_notes

    grouped: dict[str, list[MarketplaceSale]] = {}
    for line in db.query(MarketplaceSale).filter(MarketplaceSale.conversation_id.isnot(None),
                                                 MarketplaceSale.order_status.in_(PAID_STATUSES)).all():
        if line.conversation_id is not None:
            grouped.setdefault(line.conversation_id, []).append(line)
    now = utcnow()
    count = 0
    for conv in db.query(Conversation).all():
        notes = parse_notes(conv.notes)
        if notes.get("vendooStatus") != "sold":
            continue
        sale = notes.get("vendooSale")
        sale = sale if isinstance(sale, dict) else {}
        dates = notes.get("vendooDates")
        dates = dates if isinstance(dates, dict) else {}
        sold = _parse_moment(str(dates.get("sold") or sale.get("soldAt") or ""))
        sold_at = sold.astimezone(UTC).replace(tzinfo=None) if sold else None
        marketplace = _marketplace(sale, dates, notes, VENDOO_MARKETPLACE_ALIASES)
        data = {"currency": "USD", "item_revenue": _amount(sale.get("price")), "fees": _amount(sale.get("fees")),
                "item_cost": _amount(sale.get("cost")), "shipping_cost": _amount(sale.get("shippingCost")),
                # Vendoo's sale has no refund field; absent buyer shipping means none.
                "shipping_credit": _amount(sale.get("shippingCredit")) or "0", "refunds": "0"}
        if sold_at is None:
            data["sold_on"] = None
        lines = sorted(grouped.get(conv.id, []), key=lambda line: (line.sold_at or line.received_at, line.id))
        match = matching_sale_index(lines, marketplace, sold)
        if match is not None and is_listing_report(lines[match]):
            # A listing that only read sold is not a record to keep: Vendoo's sale stands in for it.
            db.delete(lines[match])
            grouped[conv.id] = [line for line in lines if line is not lines[match]]
            match = None
        if match is not None:
            line = lines[match]
            raw = line.financials
            if sold_at and line.sold_at is None and not (isinstance(raw, dict) and "sold_on" in raw):
                line.sold_at = sold_at
            if raw is None:
                db.flush()
                line.financials = {**data, "order_basis": order_basis(line), "updated_at": now.isoformat(), "source": "vendoo"}
                line.financial_revision = (line.financial_revision or 0) + 1
                count += 1
            continue
        line = MarketplaceSale(id=f"vendoo-{conv.id}", marketplace=marketplace, order_id=ORDER_ID, line_id=conv.id,
                               listing_id=None, quantity=1, order_status="paid", source="vendoo", state="dismissed",
                               conversation_id=conv.id, sold_at=sold_at, received_at=conv.created_at or now,
                               resolved_at=now, financial_revision=1)
        db.add(line)
        db.flush()
        line.financials = {**data, "order_basis": order_basis(line), "updated_at": now.isoformat(), "source": "vendoo"}
        grouped.setdefault(conv.id, []).append(line)
        count += 1
    db.commit()
    return count
