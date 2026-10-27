"""Streaming, whitelisted CSV exports. Spreadsheet formulas are text, not code."""
from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
import io

from sqlalchemy.orm import Session

from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.listing import Listing, ListingRevision
from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
from vendoo_studio.services import sales_ledger
from vendoo_studio.services.inventory_analytics import load_rows
from vendoo_studio.services.marketplace_delist import current_listing
from vendoo_studio.services.marketplace_identity import listing_reference
from vendoo_studio.services.vendoo_import import parse_notes


def text(value) -> str:
    value = value if isinstance(value, str) else ""
    probe = value.lstrip("\ufeff \t\r\n")
    if probe.startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


def number(value) -> str:
    if value is None or isinstance(value, bool):
        return ""
    try:
        amount = Decimal(str(value))
    except (ValueError, InvalidOperation):
        return ""
    return format(amount, "f") if amount.is_finite() else ""


def _csv(headers, rows):
    yield "\ufeff"
    with io.StringIO(newline="") as buffer:
        writer = csv.writer(buffer)
        for row in (headers,):
            writer.writerow(row)
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)
        for row in rows:
            writer.writerow(row)
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)


def sales_csv(query):
    headers = ["sale_id", "marketplace", "order_id", "line_id", "listing_id", "quantity", "order_status", "alert_state",
               "conversation_id", "title", "sold_at_utc", "received_at_utc", "currency", *sales_ledger.AMOUNTS,
               "net_revenue", "profit", "financials_need_review", "financials_revision", "financials_updated_at_utc"]

    def rows():
        for sale, title in query.yield_per(500):
            record = sales_ledger.sale_out(sale, title)
            data = record["financials"] or {}
            yield [text(record["id"]), text(record["marketplace"]), text(record["order_id"]), text(record["line_id"]),
                   text(record["listing_id"]), number(record["quantity"]), text(record["order_status"]), text(record["alert_state"]),
                   text(record["conversation_id"]), text(title), text(record["sold_at"]), text(record["received_at"]), text(data.get("currency")),
                   *[number(data.get(field)) for field in sales_ledger.AMOUNTS], number(record["net_revenue"]), number(record["profit"]),
                   str(record["financials_stale"]).lower(), number(record["financial_revision"]), text(record["financials_updated_at"])]
    return _csv(headers, rows())


def inventory_csv(db: Session):
    headers = ["conversation_id", "title", "description", "local_status", "sku", "price", "recorded_cost", "local_quantity", "brand", "category",
               *[f"saved_{marketplace}_url" for marketplace in sales_ledger.MARKETPLACES]]
    # The item's own row comes first; a sale row after it carries that sale's cost.
    costs = {}
    for row in load_rows(db):
        costs.setdefault(row.conversation_id, row.cost)
    links = MarketplaceListingRepo(db).listing_urls()
    query = db.query(Conversation, ListingRevision.listing_json).outerjoin(Listing, Listing.conversation_id == Conversation.id).outerjoin(
        ListingRevision, ListingRevision.id == Listing.current_revision_id,
    ).order_by(Conversation.created_at, Conversation.id)

    def rows():
        for conv, raw in query.yield_per(500):
            listing = raw if isinstance(raw, dict) else {}
            notes = parse_notes(conv.notes)
            imported, marks = notes.get("vendooListingUrls"), notes.get("marketplaceMarks")
            urls = []
            for marketplace in sales_ledger.MARKETPLACES:
                mark = marks.get(marketplace) if isinstance(marks, dict) else None
                url, _source = current_listing(marketplace, links.get(conv.id, {}).get(marketplace),
                                               imported.get(marketplace) if isinstance(imported, dict) else None,
                                               mark if isinstance(mark, dict) else {})
                reference = listing_reference(marketplace, url)
                urls.append(text(reference[1]) if reference else "")
            yield [text(conv.id), text(conv.title), text(listing.get("description")), text(conv.status), text(listing.get("sku")),
                   number(listing.get("price")), number(costs.get(conv.id)), number(listing.get("quantity")),
                   text(listing.get("brand")), text(listing.get("category_path")), *urls]
    return _csv(headers, rows())
