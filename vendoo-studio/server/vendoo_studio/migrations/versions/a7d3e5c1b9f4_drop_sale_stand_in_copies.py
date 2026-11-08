"""Remove Vendoo and Mark as sold copies of sales a marketplace order records

Revision ID: a7d3e5c1b9f4
Revises: e5b81d27c4a6
Create Date: 2026-11-08 12:00:00.000000

Until now only the Chrome order history replaced the Vendoo sale or the
seller's Mark as sold that stood for a marketplace order. An eBay or Etsy
order line linked to its item by a webhook, Recover orders or a later match
left the copy in place, so the sale was counted twice.

For each seller, item and marketplace, each copy goes to the nearest order
line by date that no other copy has taken, and is deleted. The order line
keeps the amounts the seller edited on the copy whole (with its sale date when
they set one); otherwise it takes each amount it lacks, such as the item cost,
from the copy. A copy on another marketplace, or with no order line left to
pair with, stays. The matching is frozen here so this revision does not change
when the app's code does. Nothing on any marketplace changes.
"""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json

from alembic import op
import sqlalchemy as sa


revision = 'a7d3e5c1b9f4'
down_revision = 'e5b81d27c4a6'
branch_labels = None
depends_on = None

AMOUNTS = ("item_revenue", "shipping_credit", "refunds", "fees", "shipping_cost", "item_cost")
BASIS_FIELDS = ("marketplace", "order_id", "line_id", "listing_id", "conversation_id", "quantity", "order_status",
                "order_updated_at", "order_observed_at")

sales = sa.table(
    "marketplace_sales",
    sa.column("id", sa.String), sa.column("user_id", sa.String), sa.column("marketplace", sa.String),
    sa.column("order_id", sa.String), sa.column("line_id", sa.String), sa.column("listing_id", sa.String),
    sa.column("conversation_id", sa.String), sa.column("quantity", sa.Integer), sa.column("order_status", sa.String),
    sa.column("order_updated_at", sa.DateTime), sa.column("order_observed_at", sa.DateTime),
    sa.column("sold_at", sa.DateTime), sa.column("received_at", sa.DateTime), sa.column("source", sa.String),
    sa.column("financials", sa.JSON), sa.column("financial_revision", sa.Integer),
)


def _iso(value):
    return value.replace(tzinfo=UTC).isoformat() if isinstance(value, datetime) else value


def _order_basis(row: dict) -> str:
    values = [_iso(row[field]) for field in BASIS_FIELDS]
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def _raw(row: dict) -> dict:
    return row["financials"] if isinstance(row["financials"], dict) else {}


def _is_order_line(row: dict) -> bool:
    """An order the marketplace reported, not a listing the old sold check read as sold."""
    listing_report = row["order_id"] == row["line_id"] == row["listing_id"] and _raw(row).get("source") != "seller"
    return row["source"] == "marketplace" and not listing_report


def _moment(row: dict) -> datetime | None:
    return row["sold_at"] or row["received_at"]


def _carry_over(line: dict, old: dict) -> bool:
    raw, kept = _raw(line), _raw(old)
    if not kept or raw.get("source") == "seller":
        return False
    if kept.get("source") == "seller":
        if "sold_on" in kept:
            line["sold_at"] = old["sold_at"]
        data = {**kept, "order_basis": _order_basis(line)}
    else:
        data = dict(raw) or {"currency": kept.get("currency"), "order_basis": _order_basis(line),
                             "updated_at": kept.get("updated_at"), "source": kept.get("source")}
        for field in AMOUNTS:
            if data.get(field) is None and kept.get(field) is not None:
                data[field] = kept[field]
        if data == raw:
            return False
    line["financials"] = data
    line["financial_revision"] = (line["financial_revision"] or 0) + 1
    return True


def upgrade() -> None:
    bind = op.get_bind()
    rows = [dict(row) for row in bind.execute(sa.select(sales).where(
        sales.c.conversation_id.isnot(None), sales.c.source.in_(("marketplace", "seller", "vendoo")))).mappings()]
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        groups.setdefault((row["user_id"], row["conversation_id"], row["marketplace"]), []).append(row)
    for group in groups.values():
        copies = sorted((row for row in group if row["source"] in ("seller", "vendoo")),
                        key=lambda row: (_moment(row) or datetime.min, row["id"]))
        lines = [row for row in group if _is_order_line(row)]
        for old in copies:
            if not lines:
                break
            when = _moment(old)
            line = min(lines, key=lambda row: abs((_moment(row) - when).total_seconds())
                       if when and _moment(row) else float("inf"))
            lines.remove(line)
            if _carry_over(line, old):
                bind.execute(sales.update().where(sales.c.user_id == line["user_id"], sales.c.id == line["id"]).values(
                    financials=line["financials"], financial_revision=line["financial_revision"], sold_at=line["sold_at"]))
            bind.execute(sales.delete().where(sales.c.user_id == old["user_id"], sales.c.id == old["id"]))


def downgrade() -> None:
    """The removed copies duplicated sales the marketplace records; they are not restored."""
