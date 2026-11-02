"""Read-only sales view combining imported Vendoo history with direct orders."""
from datetime import UTC
import hashlib
import json
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.marketplace_sale import PAID_STATUSES, MarketplaceSale
from vendoo_studio.services import sales_ledger
from vendoo_studio.services.inventory_analytics import AnalyticsItem, _marketplace, load_rows
from vendoo_studio.services.sale_matching import matching_sale_index
from vendoo_studio.services.sell_through import _parse_moment
from vendoo_studio.services.vendoo_import import VENDOO_MARKETPLACE_ALIASES, parse_notes

FIELD_LABELS = {"item_revenue": "sale price", "shipping_credit": "shipping collected", "refunds": "refunds",
                "fees": "fees", "shipping_cost": "shipping cost", "item_cost": "item cost"}


def missing_fields(record: dict) -> list[str]:
    data = record["financials"] or {}
    missing = [label for field, label in FIELD_LABELS.items() if data.get(field) is None]
    return missing + (["sale date"] if not record["sold_at"] else [])


def missing_by_conversation(db: Session, conversation_id: str | None = None) -> dict[str, list[str]]:
    grouped: dict[str, list[dict]] = {}
    for row in records(db, conversation_id):
        if row["conversation_id"] and row["order_status"] in PAID_STATUSES:
            grouped.setdefault(row["conversation_id"], []).append(row)
    result = {}
    for conv_id, rows in grouped.items():
        # Listings cleared on other marketplaces are not additional sales.
        backed = [row for row in rows if row["financials"] is not None or row["order_id"] != row["line_id"]]
        missing = {field for row in (backed or rows) for field in missing_fields(row)}
        result[conv_id] = [label for label in [*FIELD_LABELS.values(), "sale date"] if label in missing]
    return result


def _amount(value) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    return format(amount, "f") if amount.is_finite() else None


def incomplete_sales(db: Session, rows: list[AnalyticsItem] | None = None) -> list[dict]:
    """Every sale record, across all time, that Sales records' Missing sale data filter shows."""
    return sorted(
        ({"id": row["id"], "conversation_id": row["conversation_id"],
          "title": row["title"] or f"Order {row['order_id']}", "missing": row["missing_sale_data"]}
         for row in records(db, rows=rows) if row["missing_sale_data"]),
        key=lambda row: (row["title"].casefold(), row["id"]),
    )


def records(db: Session, conversation_id: str | None = None, rows: list[AnalyticsItem] | None = None) -> list[dict]:
    """``rows`` lets a caller that already loaded Analytics' rows for the same scope reuse them."""
    # Reuse Analytics' cost basis, including box allocations and order quantities.
    costs = {row.sale_id or f"vendoo-{row.conversation_id}": _amount(row.cost)
             for row in (load_rows(db, conversation_id) if rows is None else rows) if row.status == "sold"}
    query = db.query(MarketplaceSale, Conversation.title).outerjoin(Conversation, Conversation.id == MarketplaceSale.conversation_id)
    conversations = db.query(Conversation)
    if conversation_id:
        query = query.filter(MarketplaceSale.conversation_id == conversation_id)
        conversations = conversations.filter(Conversation.id == conversation_id)
    out: dict[str, dict] = {}
    grouped: dict[str, list[MarketplaceSale]] = {}
    for line, title in query.order_by(MarketplaceSale.received_at, MarketplaceSale.id).all():
        out[line.id] = sales_ledger.sale_out(line, title)
        if line.conversation_id and line.order_status in PAID_STATUSES:
            grouped.setdefault(line.conversation_id, []).append(line)
    for conv in conversations.all():
        notes = parse_notes(conv.notes)
        if notes.get("vendooStatus") != "sold":
            continue
        sale = notes.get("vendooSale")
        sale = sale if isinstance(sale, dict) else {}
        dates = notes.get("vendooDates")
        dates = dates if isinstance(dates, dict) else {}
        sold_at = _parse_moment(str(dates.get("sold") or sale.get("soldAt") or ""))
        marketplace = _marketplace(sale, dates, notes, VENDOO_MARKETPLACE_ALIASES)
        lines = sorted(grouped.get(conv.id, []), key=lambda line: (line.sold_at or line.received_at, line.id))
        match = matching_sale_index(lines, marketplace, sold_at)
        raw = lines[match].financials if match is not None else None
        if match is not None and sold_at and not (isinstance(raw, dict) and "sold_on" in raw):
            out[lines[match].id]["sold_at"] = sold_at.astimezone(UTC).isoformat()
        # A local financial record, even a partial or stale one, belongs to the seller.
        if match is not None and lines[match].financials is not None:
            continue
        data = {"currency": "USD", "item_revenue": _amount(sale.get("price")),
                "fees": _amount(sale.get("fees")), "item_cost": _amount(sale.get("cost")),
                "shipping_cost": _amount(sale.get("shippingCost")),
                # Vendoo's sale model has no refund field; absent buyer shipping means none.
                "shipping_credit": _amount(sale.get("shippingCredit")) or "0", "refunds": "0"}
        if match is not None:
            record = out[lines[match].id]
        else:
            record = {"id": f"vendoo-{conv.id}", "marketplace": marketplace, "order_id": "Vendoo import", "line_id": conv.id,
                      "listing_id": None, "quantity": 1, "order_status": "paid", "source": "vendoo", "alert_state": "dismissed",
                      "conversation_id": conv.id, "title": conv.title, "sold_at": None,
                      "received_at": sales_ledger._iso(conv.created_at), "financial_revision": 0,
                      "financials_updated_at": None, "order_basis": ""}
            out[record["id"]] = record
        effective_cost = costs.get(record["id"])
        if effective_cost is not None and (data["item_cost"] is None or Decimal(effective_cost) != Decimal(data["item_cost"])):
            data["item_cost"] = effective_cost
        record.update(financials=data, financials_stale=False, financials_source="vendoo")
        record["net_revenue"], record["profit"] = sales_ledger.totals(data)
        if sold_at:
            record["sold_at"] = sold_at.astimezone(UTC).isoformat()
        if match is None:
            record["order_basis"] = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
    for record in out.values():
        record["missing_sale_data"] = missing_fields(record)
    return list(out.values())
