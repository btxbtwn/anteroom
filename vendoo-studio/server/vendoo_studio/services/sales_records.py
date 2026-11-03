"""Read-only sales view: every line on the sales ledger."""
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.marketplace_sale import PAID_STATUSES, MarketplaceSale
from vendoo_studio.services import sales_ledger
from vendoo_studio.services.inventory_analytics import AnalyticsItem, load_rows

# Named as Mark as sold and the item's form name them. Refunds are not asked for:
# blank counts as none, and marketplaces fill them in when they happen.
FIELD_LABELS = {"item_revenue": "sale price", "shipping_credit": "shipping the buyer paid", "fees": "fees",
                "shipping_cost": "shipping you paid", "item_cost": "cost of goods"}


def missing_fields(record: dict) -> list[str]:
    data = record["financials"] or {}
    missing = [label for field, label in FIELD_LABELS.items() if data.get(field) is None]
    return missing + (["sale date"] if not record["sold_at"] else [])


def missing_by_conversation(db: Session, conversation_id: str | None = None) -> dict[str, list[str]]:
    return missing_from_records(records(db, conversation_id))


def missing_from_records(records: list[dict]) -> dict[str, list[str]]:
    grouped: dict[str, list[dict]] = {}
    for row in records:
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
    # Whole amounts read as the seller typed them: 5, not 5.0.
    return format(amount.normalize() if amount == amount.to_integral() else amount, "f") if amount.is_finite() else None


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
    return records_from_lines(read_sale_lines(db, conversation_id), analytics_rows)


def read_sale_lines(db: Session, conversation_id: str | None = None) -> list:
    query = db.query(MarketplaceSale, Conversation.title).outerjoin(Conversation, Conversation.id == MarketplaceSale.conversation_id)
    if conversation_id:
        query = query.filter(MarketplaceSale.conversation_id == conversation_id)
    return query.order_by(MarketplaceSale.received_at, MarketplaceSale.id).all()


def records_from_lines(lines: list, analytics_rows: list[AnalyticsItem]) -> list[dict]:
    """Build the ledger view from materialized rows, without holding its session."""
    costs = {row.sale_id: _amount(row.cost) for row in analytics_rows if row.status == "sold" and row.sale_id}
    rows = []
    for line, title in lines:
        record = sales_ledger.sale_out(line, title)
        data = record["financials"]
        raw = line.financials if isinstance(line.financials, dict) else {}
        # A sale without its own item cost uses the item's Cost of Goods, as Analytics counts it.
        # Vendoo's kept sales always show the cost as Analytics counts it.
        effective_cost = costs.get(line.id)
        if data is not None and effective_cost is not None and not record["financials_stale"] \
                and (data["item_cost"] is None
                     or raw.get("source") == "vendoo" and Decimal(effective_cost) != Decimal(data["item_cost"])):
            data["item_cost"] = effective_cost
            record["net_revenue"], record["profit"] = sales_ledger.totals(data, record["financials_stale"])
        record["financials_source"] = raw.get("source")
        record["missing_sale_data"] = missing_fields(record)
        rows.append(record)
    return rows
