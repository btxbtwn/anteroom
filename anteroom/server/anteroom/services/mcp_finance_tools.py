"""Read-only MCP tools for orders, profit and loss, expenses and an inventory summary.

Each tool reads through the service its Anteroom screen uses (Sales records,
Profit & Loss, Expenses, Analytics), so an agent sees the seller's numbers,
not a second calculation. They change nothing and never reach a marketplace.
Buyer names, addresses, emails and messages are never returned.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast, get_args

from sqlalchemy import select
from sqlalchemy.orm import Session

from anteroom.models.conversation import Conversation
from anteroom.models.marketplace_listing import LIVE_STATES
from anteroom.models.marketplace_sale import ORDER_STATUSES
from anteroom.repositories.marketplace_listings import MarketplaceListingRepo
from anteroom.services import inventory_facets, profit_and_loss, sales_ledger
from anteroom.services.business_expenses import Category, entries
from anteroom.services.inventory_analytics import load_rows, summarize
from anteroom.services.marketplace_delist import removed_marketplaces
from anteroom.services.vendoo_import import parse_notes

# Longest period one call reads, so a call on a large inventory answers in time.
MAX_PERIOD_DAYS = 366
DEFAULT_PERIOD_DAYS = 30
MAX_PAGE = 100
MAX_OFFSET = 10_000
CATEGORIES = get_args(Category)
_P_AND_L_MODES = get_args(profit_and_loss.AccountingMode)

_READ = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
_DATE = {"type": "string", "description": "ISO date YYYY-MM-DD, UTC."}
_PAGE = {
    "limit": {"type": "integer", "minimum": 1, "maximum": MAX_PAGE, "default": 25},
    "offset": {"type": "integer", "minimum": 0, "maximum": MAX_OFFSET, "default": 0},
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_orders",
        "title": "Search orders",
        "description": (
            "List individual sales (order lines) across eBay, Etsy, Poshmark, Mercari, Depop and sales recorded "
            "elsewhere, as Sales records shows them: marketplace, date, item (ID, title, SKU), status, sale price, "
            "fees, shipping, net, cost and profit, with totals for every match. Filter by date range (default the "
            f"last {DEFAULT_PERIOD_DAYS} days, at most {MAX_PERIOD_DAYS} days), marketplace, text (title, SKU or "
            "order ID) and order status. No buyer details. For period KPIs use get_sales_analytics; for the "
            "income statement use get_profit_and_loss."
        ),
        "annotations": _READ,
        "inputSchema": {
            "type": "object",
            "properties": {
                "start": {**_DATE, "description": f"First day, ISO YYYY-MM-DD (default {DEFAULT_PERIOD_DAYS - 1} days before end)."},
                "end": {**_DATE, "description": "Last day, ISO YYYY-MM-DD, today or earlier (default today, UTC)."},
                "marketplace": {"type": "string", "enum": list(sales_ledger.SALE_MARKETPLACES),
                                "description": "Only sales on this marketplace; 'other' is a sale recorded elsewhere."},
                "query": {"type": "string", "maxLength": 200, "description": "Text in the item title, SKU or order ID."},
                "status": {"type": "string", "enum": list(ORDER_STATUSES), "description": "Only orders in this status."},
                "sort": {"type": "string", "enum": ["newest", "oldest"], "default": "newest"},
                **_PAGE,
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_profit_and_loss",
        "title": "Get profit and loss",
        "description": (
            "Read the Profit & Loss report for a period, exactly as Anteroom's Profit & Loss screen shows it: "
            "gross sales, collected shipping, refunds, net revenue, cost of goods (accrual) or inventory "
            "purchases (cash), marketplace fees, shipping costs, operating expenses by category and net profit, "
            "one report per currency, with how many sales lack figures. Default period is this month to date; "
            f"at most {MAX_PERIOD_DAYS} days, ending today or earlier. For the individual sales use search_orders; "
            "for the individual expenses use search_expenses."
        ),
        "annotations": _READ,
        "inputSchema": {
            "type": "object",
            "properties": {
                "start": {**_DATE, "description": "First day, ISO YYYY-MM-DD (default the 1st of this month)."},
                "end": {**_DATE, "description": "Last day, ISO YYYY-MM-DD, today or earlier (default today, UTC)."},
                "mode": {"type": "string", "enum": list(_P_AND_L_MODES), "default": "accrual",
                         "description": "accrual counts cost of goods sold; cash counts inventory purchases when paid."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "search_expenses",
        "title": "Search business expenses",
        "description": (
            "List the seller's business expenses (supplies, labels, fees, subscriptions, mileage, inventory "
            "purchases) with totals per currency and category for every match. Filter by date range (default the "
            f"last {DEFAULT_PERIOD_DAYS} days, at most {MAX_PERIOD_DAYS} days), category and text in the vendor, "
            "description or notes. For how expenses affect profit use get_profit_and_loss."
        ),
        "annotations": _READ,
        "inputSchema": {
            "type": "object",
            "properties": {
                "start": {**_DATE, "description": f"First day, ISO YYYY-MM-DD (default {DEFAULT_PERIOD_DAYS - 1} days before end)."},
                "end": {**_DATE, "description": "Last day, ISO YYYY-MM-DD (default today, UTC)."},
                "category": {"type": "string", "enum": list(CATEGORIES)},
                "query": {"type": "string", "maxLength": 200, "description": "Text in the vendor, description or notes."},
                **_PAGE,
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_inventory_summary",
        "title": "Get inventory summary",
        "description": (
            "Snapshot of the inventory right now: item counts by status (active, draft, sold, failed, working), "
            "live items and asking value per marketplace, total asking value and cost basis of active stock, "
            "and how long active items have been listed (age buckets, oldest items). Takes no period. For sales "
            "over a period use get_sales_analytics; to find specific items use search_inventory."
        ),
        "annotations": _READ,
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]

PERMISSIONS: dict[str, tuple[str, str]] = {tool["name"]: ("analytics", "read") for tool in TOOLS}


def _date(args: dict[str, Any], key: str) -> date | None:
    value = args.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be an ISO date string (YYYY-MM-DD).")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"{key} must be an ISO date string (YYYY-MM-DD).") from exc


def _period(args: dict[str, Any], default_start) -> tuple[date, date]:
    end = _date(args, "end") or datetime.now(UTC).date()
    start = _date(args, "start") or default_start(end)
    if end < start:
        raise ValueError("end must be on or after start.")
    if (end - start).days + 1 > MAX_PERIOD_DAYS:
        raise ValueError(f"Choose a period of at most {MAX_PERIOD_DAYS} days; ask for longer periods one year at a time.")
    return start, end


def _last_days(end: date) -> date:
    return end - timedelta(days=DEFAULT_PERIOD_DAYS - 1)


def _int(args: dict[str, Any], key: str, default: int, maximum: int, minimum: int = 0) -> int:
    value = args.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer.")
    return max(minimum, min(maximum, value))


def _choice(args: dict[str, Any], key: str, allowed, default: str | None = None) -> str | None:
    value = args.get(key)
    if value is None or value == "":
        return default
    if not isinstance(value, str) or value.strip().lower() not in allowed:
        raise ValueError(f"{key} must be one of: {', '.join(allowed)}.")
    return value.strip().lower()


def _text(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > 200:
        raise ValueError(f"{key} must be text of at most 200 characters.")
    return value.strip().casefold()


def _amount(cents: int) -> str:
    return format(Decimal(cents) / 100, ".2f")


def _search_orders(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    start, end = _period(args, _last_days)
    marketplace = _choice(args, "marketplace", sales_ledger.SALE_MARKETPLACES)
    status = _choice(args, "status", ORDER_STATUSES)
    sort = _choice(args, "sort", ("newest", "oldest"), "newest") or "newest"
    query = _text(args, "query")
    limit = _int(args, "limit", 25, MAX_PAGE, minimum=1)
    offset = _int(args, "offset", 0, MAX_OFFSET)
    rows = sales_ledger.sale_records(db, start, end, marketplace, sort=sort)
    skus = {conv_id: facet["sku"] for conv_id, facet in inventory_facets.listing_facets(db).items() if facet["sku"]}
    matched = [
        row for row in rows
        if (status is None or row["order_status"] == status)
        and (not query or query in " ".join(
            str(value or "") for value in (row["title"], skus.get(row["conversation_id"]), row["order_id"])
        ).casefold())
    ]
    totals = sales_ledger.summary(matched)
    page = matched[offset:offset + limit]
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "total": len(matched),
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < len(matched),
        "summary": {key: totals[key] for key in ("lines", "unmatched", "needs_review", "missing_records", "currencies")},
        "orders": [_order(row, skus.get(row["conversation_id"])) for row in page],
    }


def _order(row: dict[str, Any], sku: str | None) -> dict[str, Any]:
    money = row["financials"] or {}
    return {
        "sale_id": row["id"],
        "marketplace": row["marketplace"],
        "order_id": row["order_id"],
        "sold_at": row["sold_at"] or row["received_at"],
        "status": row["order_status"],
        "quantity": row["quantity"],
        "recorded_by": row["source"],
        "item": {"conversation_id": row["conversation_id"], "title": row["title"], "sku": sku},
        "currency": money.get("currency"),
        "sale_price": money.get("item_revenue"),
        "shipping_charged": money.get("shipping_credit"),
        "refunds": money.get("refunds"),
        "fees": money.get("fees"),
        "shipping_cost": money.get("shipping_cost"),
        "net": row["net_revenue"],
        "cost": money.get("item_cost"),
        "profit": row["profit"],
        "figures_need_review": row["financials_stale"],
        "missing": row["missing_sale_data"],
    }


def _month_start(end: date) -> date:
    return end.replace(day=1)


def _profit_and_loss(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    start, end = _period(args, _month_start)
    mode = _choice(args, "mode", _P_AND_L_MODES, "accrual") or "accrual"
    reports = profit_and_loss.report(db, start, end, cast(profit_and_loss.AccountingMode, mode))
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "mode": mode,
        "note": "Amounts are in each report's currency; amount_cents is exact.",
        "currencies": [{
            "currency": report.currency,
            "incomplete_sales": report.incomplete_sales,
            "lines": [{"label": line.label, "amount_cents": line.amount_cents, "amount": _amount(line.amount_cents)}
                      for line in report.lines],
        } for report in reports],
    }


def _search_expenses(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    start, end = _period(args, _last_days)
    category = _choice(args, "category", CATEGORIES)
    query = _text(args, "query")
    limit = _int(args, "limit", 25, MAX_PAGE, minimum=1)
    offset = _int(args, "offset", 0, MAX_OFFSET)
    rows = [
        row for row in db.scalars(entries(start, end, cast(Category | None, category)))
        if not query or query in f"{row.vendor} {row.description} {row.notes}".casefold()
    ]
    totals: dict[str, dict[str, Any]] = {}
    for row in rows:
        group = totals.setdefault(row.currency, {"count": 0, "amount_cents": 0, "by_category": {}})
        group["count"] += 1
        group["amount_cents"] += row.amount_cents
        by = group["by_category"].setdefault(row.category, {"count": 0, "amount_cents": 0})
        by["count"] += 1
        by["amount_cents"] += row.amount_cents
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "total": len(rows),
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < len(rows),
        "totals": [{
            "currency": currency, "count": group["count"], "amount_cents": group["amount_cents"],
            "amount": _amount(group["amount_cents"]),
            "by_category": [{"category": name, **values, "amount": _amount(values["amount_cents"])}
                            for name, values in sorted(group["by_category"].items())],
        } for currency, group in sorted(totals.items())],
        "expenses": [{
            "id": row.id,
            "date": row.incurred_date.isoformat(),
            "amount_cents": row.amount_cents,
            "amount": _amount(row.amount_cents),
            "currency": row.currency,
            "category": row.category,
            "accounting_type": row.accounting_type,
            "vendor": row.vendor,
            "description": row.description,
            "notes": row.notes,
            "distance": format(row.distance, "f") if row.distance is not None else None,
            "distance_unit": row.distance_unit,
        } for row in rows[offset:offset + limit]],
    }


def _inventory_summary(db: Session, _args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    rows = load_rows(db)
    snapshot = summarize(rows, range_id="all", now=now)
    stock = snapshot["inventory"]
    active = {row.conversation_id: row.price for row in rows if row.status == "active" and row.stock}
    per_market: dict[str, dict[str, Any]] = {}
    for marketplace, conv_id in _live_listings(db, set(active)):
        entry = per_market.setdefault(marketplace, {"marketplace": marketplace, "live_items": 0, "asking_value": 0.0})
        entry["live_items"] += 1
        entry["asking_value"] += active[conv_id]
    return {
        "as_of": now.isoformat(),
        "items_by_status": {key: int(stock[key]) for key in ("active", "draft", "sold", "failed", "working")},
        "active": {
            "asking_value": stock["asking_value"],
            "cost_basis": stock["cost_value"],
            "items_with_cost": stock["cost_known"],
            "items_without_cost": int(stock["active"]) - stock["cost_known"],
            "listed_90_days_or_more": {"count": stock["stale_count"], "asking_value": stock["stale_value"]},
            "no_list_date": stock["undated_count"],
        },
        "marketplaces": sorted(({**entry, "asking_value": round(entry["asking_value"], 2)} for entry in per_market.values()),
                               key=lambda entry: (-entry["live_items"], entry["marketplace"])),
        "aging": [{key: row[key] for key in ("label", "count", "asking_value")} for row in snapshot["aging"]],
        "oldest": snapshot["oldest"],
    }


def _live_listings(db: Session, conv_ids: set[str]) -> list[tuple[str, str]]:
    """(marketplace, item) for each active item's live listings, as search_inventory reads them."""
    direct = MarketplaceListingRepo(db).summaries()
    live: list[tuple[str, str]] = []
    for conv_id, raw_notes in db.execute(select(Conversation.id, Conversation.notes)):
        if conv_id not in conv_ids:
            continue
        notes = parse_notes(raw_notes)
        mine = direct.get(conv_id) or {}
        sold = {m for m, rec in mine.items() if rec.get("state") == "sold"} | {
            str(m).lower() for m, when in ((notes.get("vendooDates") or {}).get("soldByMarketplace") or {}).items() if when
        }
        removed = set(removed_marketplaces(notes, {m: str(rec["listing_url"]) for m, rec in mine.items() if rec.get("listing_url")}))
        imported = {str(m).lower() for m in (notes.get("vendooMarketplaces") or []) if isinstance(m, str)}
        markets = ({m for m, rec in mine.items() if rec.get("state") in LIVE_STATES} | (imported - removed)) - sold
        live.extend((marketplace, conv_id) for marketplace in sorted(markets))
    return live


HANDLERS = {
    "search_orders": _search_orders,
    "get_profit_and_loss": _profit_and_loss,
    "search_expenses": _search_expenses,
    "get_inventory_summary": _inventory_summary,
}
