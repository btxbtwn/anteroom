"""Read-only sales view: every line on the sales ledger."""
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.marketplace_sale import PAID_STATUSES, MarketplaceSale
from vendoo_studio.services import sales_ledger
from vendoo_studio.services.inventory_analytics import AnalyticsItem, load_rows

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
         for row in records(db, analytics_rows=rows) if row["missing_sale_data"]),
        key=lambda row: (row["title"].casefold(), row["id"]),
    )


def records(db: Session, conversation_id: str | None = None, analytics_rows: list[AnalyticsItem] | None = None) -> list[dict]:
    """Every line on the sales ledger. Vendoo's sales are read from the ledger too, where they were kept once."""
    from vendoo_studio.services.vendoo_sales_archive import ensure_archived

    ensure_archived(db)
    # Reuse Analytics' cost basis, including box allocations and order quantities.
    analytics_rows = load_rows(db, conversation_id) if analytics_rows is None else analytics_rows
    costs = {row.sale_id: _amount(row.cost) for row in analytics_rows if row.status == "sold" and row.sale_id}
    query = db.query(MarketplaceSale, Conversation.title).outerjoin(Conversation, Conversation.id == MarketplaceSale.conversation_id)
    if conversation_id:
        query = query.filter(MarketplaceSale.conversation_id == conversation_id)
    rows = []
    for line, title in query.order_by(MarketplaceSale.received_at, MarketplaceSale.id).all():
        record = sales_ledger.sale_out(line, title)
        data = record["financials"]
        raw = line.financials if isinstance(line.financials, dict) else {}
        # Vendoo's kept sales show the item's cost as Analytics counts it, as they always have.
        effective_cost = costs.get(line.id)
        if data is not None and raw.get("source") == "vendoo" and effective_cost is not None \
                and (data["item_cost"] is None or Decimal(effective_cost) != Decimal(data["item_cost"])):
            data["item_cost"] = effective_cost
            record["net_revenue"], record["profit"] = sales_ledger.totals(data, record["financials_stale"])
        record["financials_source"] = raw.get("source")
        record["missing_sale_data"] = missing_fields(record)
        rows.append(record)
    return rows
