"""The figures and charts at the top of Sales records, for one period and marketplace.

Reads the same rows as Analytics (``inventory_analytics.load_rows``), so the
revenue, profit and costs here match the Inventory analytics tab: costs come
from the item's form, its box share and the amounts recorded on each sale.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from vendoo_studio.services.inventory_analytics import (
    AnalyticsItem,
    _groups,
    _marketplace_key,
    _money,
    _ranked_groups,
    _sales_stats,
)

MAX_WEEKS = 52


def sales_overview(
    rows: list[AnalyticsItem],
    *,
    start: date | None,
    end: date,
    marketplace: str | None = None,
) -> dict[str, Any]:
    """Totals from ``start`` through ``end`` (whole UTC days), the same number of days
    before it, sales per week, and sales by marketplace. No ``start`` means all time."""
    if start is not None and start > end:
        raise ValueError("start must be on or before end")
    last = _midnight(end) + timedelta(days=1)
    first = _midnight(start) if start is not None else None
    sold = [row for row in rows if row.status == "sold" and (marketplace is None or row.marketplace == marketplace)]
    current = [row for row in sold if _within(row, first, last, undated=first is None)]
    # The rate needs active listings, which are kept per item, not per marketplace.
    active = None if marketplace else _active_items(rows)

    previous = None
    if start is not None:
        days = (end - start).days + 1
        before_start = start - timedelta(days=days)
        before = [row for row in sold if _within(row, _midnight(before_start), _midnight(start), undated=False)]
        previous = {
            "start": before_start.isoformat(),
            "end": (start - timedelta(days=1)).isoformat(),
            # Active stock on those days was not recorded, so it has no rate.
            **_figures(before, None),
        }

    weeks, truncated = _weeks(current, first, last)
    return {
        "start": start.isoformat() if start else None,
        "end": end.isoformat(),
        "undated_sales": sum(1 for row in current if row.sold_at is None),
        "current": _figures(current, active),
        "previous": previous,
        "weeks": weeks,
        "weeks_truncated": truncated,
        "marketplaces": [
            {"id": row["id"], "count": row["count"], "revenue": row["revenue"], "revenue_known": row["revenue_known"]}
            for row in _ranked_groups(_groups(current, _marketplace_key))
        ],
    }


def _figures(sales: list[AnalyticsItem], active: int | None) -> dict[str, Any]:
    stats = _sales_stats(sales)
    days = [row.days_listed for row in sales if row.days_listed is not None]
    available = len(sales) + active if active is not None else 0
    return {
        "count": stats["count"],
        "revenue": stats["revenue"],
        "revenue_known": stats["revenue_known"],
        "profit": stats["profit"],
        "profit_known": stats["profit_known"],
        "fees_known": stats["fees_known"],
        "median_days": stats["median_days"],
        # Removal note: replaced by median_days, which Inventory analytics also shows.
        # Kept until 2027-02-01 for iPhone builds that still read it.
        "average_days": round(sum(days) / len(days), 1) if days else None,
        "days_known": len(days),
        # Sold in the period ÷ (sold in the period + listings still active today).
        "sell_through": round(len(sales) / available * 100, 1) if available else None,
    }


def _weeks(sales: list[AnalyticsItem], first: datetime | None, last: datetime) -> tuple[list[dict[str, Any]], bool]:
    dated = [row.sold_at for row in sales if row.sold_at is not None]
    begin = first or (min(_utc(moment) for moment in dated) if dated else None)
    if begin is None:
        return [], False
    monday = _midnight(begin.date()) - timedelta(days=begin.weekday())
    final = _midnight((last - timedelta(microseconds=1)).date())
    final -= timedelta(days=final.weekday())
    count = (final - monday).days // 7 + 1
    truncated = count > MAX_WEEKS
    if truncated:
        monday = final - timedelta(weeks=MAX_WEEKS - 1)
        count = MAX_WEEKS
    buckets: list[list[AnalyticsItem]] = [[] for _ in range(count)]
    for row in sales:
        if row.sold_at is None:
            continue
        index = (_utc(row.sold_at) - monday).days // 7
        if 0 <= index < count:
            buckets[index].append(row)
    return [
        {
            "start": (monday + timedelta(weeks=index)).date().isoformat(),
            "count": len(rows),
            "revenue": _money(sum(row.sold_price or 0.0 for row in rows)),
            "revenue_known": sum(1 for row in rows if row.sold_price is not None),
        }
        for index, rows in enumerate(buckets)
    ], truncated


def _active_items(rows: list[AnalyticsItem]) -> int:
    """Items with stock still listed. An item with sales and stock left is one active row."""
    return len({row.conversation_id for row in rows if row.status == "active" and row.stock})


def _within(row: AnalyticsItem, first: datetime | None, last: datetime, *, undated: bool) -> bool:
    if row.sold_at is None:
        return undated
    moment = _utc(row.sold_at)
    return moment < last and (first is None or moment >= first)


def _midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment
