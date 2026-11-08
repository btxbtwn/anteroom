"""Match a sale recorded without its order to the marketplace order line it stood for.

Vendoo's kept sale and the seller's Mark as sold record a sale with no order
of the marketplace's. When the marketplace's own order line for that item is
linked to it, by any reader (Chrome order history, an eBay or Etsy webhook,
Recover orders, or a later match of an unmatched sale), it replaces the one
nearest its date on the same marketplace, within a week of it. The item cost and any amounts the
seller typed carry over. Nothing on any marketplace changes.
"""
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from vendoo_studio.models.marketplace_sale import MarketplaceSale

STAND_IN_SOURCES = ("seller", "vendoo")


def _aware(moment: datetime | None) -> datetime | None:
    return moment.replace(tzinfo=UTC) if moment and moment.tzinfo is None else moment


def matching_sale_index(lines: list[MarketplaceSale], marketplace: str, sold_at: datetime | None) -> int | None:
    """Vendoo has no order ID: use its marketplace and nearest sale date."""
    candidates = [i for i, line in enumerate(lines) if marketplace == "unknown" or line.marketplace == marketplace]
    if not candidates:
        return None

    def distance(index: int) -> float:
        moment = _aware(lines[index].sold_at or lines[index].received_at)
        if moment and sold_at:
            return abs((moment - sold_at).total_seconds())
        return float(index)

    return min(candidates, key=distance)


def carry_over(sale: MarketplaceSale, old: MarketplaceSale) -> None:
    """Keep on ``sale`` what was recorded on ``old``, the record it replaces.

    Amounts the seller edited on ``old`` are kept whole, with its sale date
    when the seller set one. Otherwise each amount ``sale`` lacks, such as the
    item cost, is taken from ``old``. Amounts the seller edited on ``sale``
    itself are never changed.
    """
    from vendoo_studio.services.sales_ledger import AMOUNTS, order_basis

    raw = sale.financials if isinstance(sale.financials, dict) else {}
    kept = old.financials if isinstance(old.financials, dict) else {}
    if not kept or raw.get("source") == "seller":
        return
    if kept.get("source") == "seller":
        if "sold_on" in kept:
            sale.sold_at = old.sold_at
        data = {**kept, "order_basis": order_basis(sale)}
    else:
        # Amounts not yet read from the marketplace keep the old record's source, so they are read.
        data = dict(raw) or {"currency": kept.get("currency"), "order_basis": order_basis(sale),
                             "updated_at": kept.get("updated_at"), "source": kept.get("source")}
        for field in AMOUNTS:
            if data.get(field) is None and kept.get(field) is not None:
                data[field] = kept[field]
        if data == raw:
            return
    sale.financials = data
    sale.financial_revision = (sale.financial_revision or 0) + 1


def replace_stand_in(db: Session, sale: MarketplaceSale) -> MarketplaceSale | None:
    """Delete the Mark as sold or Vendoo sale this order line stood for, keeping what was recorded on it.

    ``sale`` is a marketplace order line just linked to its item. The record
    nearest its date on the same marketplace, within a week of it, is replaced, and only when no
    other order line for the item is nearer to that record: one sale is never
    replaced twice, never across marketplaces, and an order line is never
    deleted. Returns the record replaced.
    """
    from vendoo_studio.services.item_status import SAME_SALE_DAYS, within_days
    from vendoo_studio.services.order_history import is_listing_report

    if sale.source != "marketplace" or sale.conversation_id is None or is_listing_report(sale):
        return None
    same_item = db.query(MarketplaceSale).filter(MarketplaceSale.marketplace == sale.marketplace,
                                                 MarketplaceSale.conversation_id == sale.conversation_id).all()
    stand_ins = [row for row in same_item if row.source in STAND_IN_SOURCES]
    match = matching_sale_index(stand_ins, sale.marketplace, _aware(sale.sold_at or sale.received_at))
    if match is None:
        return None
    old = stand_ins[match]
    # A record from an earlier sale of a relisted item is a sale of its own.
    if not within_days(_aware(old.sold_at or old.received_at), _aware(sale.sold_at or sale.received_at), SAME_SALE_DAYS):
        return None
    lines = [row for row in same_item if row.source == "marketplace" and not is_listing_report(row)]
    nearest = matching_sale_index(lines, sale.marketplace, _aware(old.sold_at or old.received_at))
    if nearest is None or lines[nearest] is not sale:
        return None
    db.delete(old)
    db.flush()
    carry_over(sale, old)
    return old
