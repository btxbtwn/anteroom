"""Sales and inventory totals from Anteroom's own records and the imported Vendoo history.

Status comes from ``services/item_status.py``. Sales come from the sales
ledger (``marketplace_sales``, with its recorded amounts) and from the Vendoo
sale in each item's notes, one row per order. Price, cost, brand and category
live on the current listing. This reads those and nothing else, then groups
them. It calls no marketplace and not Vendoo.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from vendoo_studio.database import json_value

if TYPE_CHECKING:
    from vendoo_studio.services.sale_events import EventWindow

ANALYTICS_RANGES = ("wtd", "mtd", "ytd", "7d", "30d", "90d", "12m", "all")
_RECENT = 8
_MAX_MONTHS = 18
_MAX_DAILY_DAYS = 31
_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
_BUSY = {"in_progress", "listing", "failed"}
_VENDOO_STATUSES = {"sold", "active", "draft"}
_AGING = (
    ("Under 2 weeks", 0, 14),
    ("2–4 weeks", 14, 30),
    ("1–2 months", 30, 60),
    ("2–3 months", 60, 90),
    ("Over 3 months", 90, None),
)
# Listings this old with no sale get a deeper cut than the everyday event one.
EVENT_PERCENT = 25
# Deepest first; the first that still covers the item's cost after fees wins.
DEEP_PERCENTS = (40, 35)
# With no cost recorded there is no floor to check, so stay at the shallower cut.
UNKNOWN_COST_PERCENT = 35
# Sale-price bands, low to high, by the lowest price each starts at. Raising the
# average sale price means more of what sells landing in the upper two.
PRICE_TIERS = (
    ("budget", "Under $20", 0.0),
    ("standard", "$20–$34", 20.0),
    ("premium", "$35–$59", 35.0),
    ("high", "$60 and up", 60.0),
)
PREMIUM_PRICE = PRICE_TIERS[2][2]


@dataclass(frozen=True)
class AnalyticsItem:
    """One listing, reduced to the figures the page can show."""

    conversation_id: str
    title: str
    status: str
    price: float
    cost: float | None
    brand: str
    category: str
    sold_price: float | None
    sold_at: datetime | None
    listed_at: datetime | None
    marketplace: str
    days_listed: int | None
    fees: float | None = None
    shipping_cost: float = 0.0
    shipping_credit: float = 0.0
    box_id: str | None = None
    missing_sale_details: tuple[str, ...] = ()
    refunds: float = 0.0
    # The sales-ledger line this sale row came from, if any.
    sale_id: str | None = None
    # The seller's labels from Item Details.
    labels: tuple[str, ...] = ()
    # False for a sale of stock merged into another item: the sale counts, the
    # item does not, because its stock is counted on the item it joined.
    stock: bool = True
    # True when a marketplace order or a Vendoo sale record stands behind this
    # row, rather than a check finding a listing marked sold.
    order_backed: bool = False

    @property
    def has_cost(self) -> bool:
        return self.cost is not None

    @property
    def net(self) -> float:
        """What the sale brought in before the item's own cost."""
        return (
            (self.sold_price or 0.0) + self.shipping_credit - self.refunds
            - (self.fees or 0.0) - self.shipping_cost
        )

    @property
    def profit(self) -> float:
        """Vendoo's net profit: sold price plus shipping paid, less the rest."""
        return self.net - (self.cost or 0.0)


def inventory_analytics(
    db,
    *,
    range_id: str = "12m",
    now: datetime | None = None,
    rows: list[AnalyticsItem] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    """Totals for ``range_id`` (``wtd``, ``mtd``, ``ytd``, ``7d``, ``30d``, ``90d``, ``12m``, ``all``, or ``custom``).

    ``rows`` lets a caller that wants several ranges load the listings once.
    ``custom`` takes the first and last day to include, both in UTC.
    """
    if range_id == "custom":
        if start is None or end is None or start > end:
            raise ValueError("custom range needs a start on or before its end")
    elif range_id not in ANALYTICS_RANGES:
        raise ValueError(range_id)
    from vendoo_studio.services.ad_spend import list_entries, summarize_ads
    from vendoo_studio.services.sale_events import windows
    from vendoo_studio.services.user_settings import vendoo_inventory_synced_at

    clock = now or datetime.now(UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)
    payload = summarize(
        load_rows(db) if rows is None else rows, range_id=range_id, now=clock, events=windows(db),
        start=start, end=end,
    )
    window_start, window_end = _window(range_id, clock, start, end)
    return {
        **payload,
        "ads": summarize_ads(
            list_entries(db),
            start=window_start,
            end=window_end if range_id == "custom" else None,
            marketplace_revenue={row["id"]: row["revenue"] for row in payload["marketplaces"]},
            marketplace_profit={row["id"]: row["profit"] for row in payload["marketplaces"]},
            profit=payload["sales"]["profit"],
        ),
        "last_updated_at": vendoo_inventory_synced_at(),
    }


def load_rows(db) -> list[AnalyticsItem]:
    """Every listing as analytics rows. Current revision only.

    An item that is not sold is one row. Each sale is a row of its own with
    status ``sold``: Anteroom's sales ledger (``marketplace_sales``, with the
    amounts recorded on it) plus the imported Vendoo sale. When both hold the
    same order, one row stands for it (see ``_item_sales``). An item with some
    units sold and some left is an active row plus a row per sale.
    """
    from vendoo_studio.models.conversation import Conversation
    from vendoo_studio.models.listing import Listing, ListingRevision
    from vendoo_studio.models.sourcing import SourceBox
    from vendoo_studio.services.item_status import paid_lines, statuses
    from vendoo_studio.services.sell_through import _days_between, _parse_moment
    from vendoo_studio.services.vendoo_import import VENDOO_MARKETPLACE_ALIASES, parse_notes, split_vendoo_labels

    facets = {
        conv_id: (price, cost, brand, category)
        for conv_id, price, cost, brand, category in (
            db.query(
                Listing.conversation_id,
                json_value(ListingRevision.listing_json, "price"),
                json_value(ListingRevision.listing_json, "cost"),
                json_value(ListingRevision.listing_json, "brand"),
                json_value(ListingRevision.listing_json, "category_path"),
            )
            .join(ListingRevision, ListingRevision.id == Listing.current_revision_id)
            .all()
        )
    }
    # An item with no cost of its own carries its share of the box it came from.
    # Pieces that were not worth listing add their cost to the ones that were.
    box_share = {box.id: _money(box.landed / box.sellable) for box in db.query(SourceBox).all() if box.sellable}
    rows: list[AnalyticsItem] = []
    conversations = [
        (conv_id, title, status, parse_notes(notes_raw), box_id)
        for conv_id, title, status, notes_raw, box_id in db.query(
            Conversation.id,
            Conversation.title,
            Conversation.status,
            Conversation.notes,
            Conversation.box_id,
        ).all()
    ]
    own = statuses(db, [(conv_id, notes) for conv_id, _title, _status, notes, _box in conversations])
    lines = paid_lines(db)
    for conv_id, title, status, notes, box_id in conversations:
        price, cost, brand, category = facets.get(conv_id, (None, None, None, None))
        dates = notes.get("vendooDates")
        if not isinstance(dates, dict):
            dates = {}
        sale = notes.get("vendooSale")
        if not isinstance(sale, dict):
            sale = {}
        listed_text = str(dates.get("listed") or "")
        derived = own[conv_id]
        effective = str(status) if str(status or "") in _BUSY else derived.status
        sale_cost = _amount(sale.get("cost"))
        own_cost = sale_cost if sale_cost is not None else _amount(cost)
        unit_cost = own_cost if own_cost is not None else box_share.get(box_id)
        base = AnalyticsItem(
            conversation_id=conv_id,
            title=(str(title or "").strip() or "Untitled listing"),
            status=effective,
            price=_number(price),
            cost=unit_cost,
            brand=str(brand or "").strip(),
            category=_category_label(category),
            sold_price=None,
            sold_at=None,
            listed_at=_parse_moment(listed_text),
            marketplace=_marketplace(sale, dates, notes, VENDOO_MARKETPLACE_ALIASES),
            days_listed=None,
            box_id=box_id,
            labels=tuple(split_vendoo_labels(notes.get("vendooLabels"))),
        )
        vendoo = None
        if str(notes.get("vendooStatus") or "") == "sold":
            sold_text = str(dates.get("sold") or sale.get("soldAt") or "")
            vendoo = replace(
                base,
                status="sold",
                order_backed=True,
                sold_price=_amount(sale.get("price")),
                sold_at=_parse_moment(sold_text),
                days_listed=_days_between(listed_text, sold_text),
                fees=_amount(sale.get("fees")),
                shipping_cost=_amount(sale.get("shippingCost")) or 0.0,
                shipping_credit=_amount(sale.get("shippingCredit")) or 0.0,
                missing_sale_details=tuple(str(gap) for gap in sale.get("missing") or ()),
            )
        # A send in flight keeps its one row, as before; its sales count once it settles.
        sales = [] if effective in _BUSY else _item_sales(base, vendoo, lines.get(conv_id, []), listed_text)
        if effective == "sold":
            # Nothing is left to sell: reports from the other marketplaces are the
            # same sale being cleared, not more stock going out.
            sales = _stocked_sales(sales, derived.quantity)
        # Unsold stock merged into another item is counted there; its sales still count here.
        merged_away = bool(notes.get("mergedInto")) and effective != "sold"
        if (effective != "sold" or not sales) and not merged_away:
            # Not sold, or sold with no sale on record (a bare Vendoo label or mark).
            fallback_sold = effective == "sold" and not sales
            rows.append(replace(base, sold_at=derived.sold_at) if fallback_sold else base)
        rows.extend([replace(sale, stock=False) for sale in sales] if merged_away else sales)
    return rows


def _stocked_sales(sales: list[AnalyticsItem], quantity: int) -> list[AnalyticsItem]:
    """A sold item's sales cannot outnumber the units it had in stock.

    When the seller sells on one marketplace and clears the item's other
    listings, each check that reads one of those listings as sold used to add
    a sale of its own. Those rows have no order behind them. Keep every sale
    that does, and only as many of the rest as the item had units; a genuine
    relist that sold again has its own order or listing report.
    """
    limit = max(1, quantity)
    ordered = [sale for sale in sales if sale.order_backed]
    reported = [sale for sale in sales if not sale.order_backed]
    room = max(0, limit - len(ordered))
    keep = {id(sale) for sale in ordered} | {id(sale) for sale in reported[:room]}
    return [sale for sale in sales if id(sale) in keep]


def _item_sales(base: AnalyticsItem, vendoo: AnalyticsItem | None, lines: list, listed_text: str) -> list[AnalyticsItem]:
    """One row per sale. A Vendoo sale and a ledger line for the same order become one.

    Vendoo keeps one sale per item and no order ID, so the same order is the
    ledger line on Vendoo's marketplace (any marketplace when Vendoo did not
    say) closest in time to Vendoo's sale. That row takes the ledger's recorded
    amounts and fills any it lacks from Vendoo.
    """
    from vendoo_studio.services.sell_through import _days_between

    ordered = sorted(lines, key=lambda line: (line.sold_at or line.received_at, line.id))
    rows = [_ledger_row(base, line, listed_text, _days_between) for line in ordered]
    if vendoo is None:
        return rows
    candidates = [index for index, line in enumerate(ordered)
                  if vendoo.marketplace == "unknown" or line.marketplace == vendoo.marketplace]
    if not candidates:
        return [vendoo, *rows]

    def distance(index: int) -> float:
        moment = rows[index].sold_at
        return abs((moment - vendoo.sold_at).total_seconds()) if moment and vendoo.sold_at else float(index)

    match = min(candidates, key=distance)
    ledger = rows[match]
    recorded = ledger.sold_price is not None or ledger.fees is not None
    rows[match] = replace(
        ledger,
        sold_price=ledger.sold_price if ledger.sold_price is not None else vendoo.sold_price,
        fees=ledger.fees if ledger.fees is not None else vendoo.fees,
        shipping_cost=ledger.shipping_cost if recorded else vendoo.shipping_cost,
        shipping_credit=ledger.shipping_credit if recorded else vendoo.shipping_credit,
        cost=ledger.cost if ledger.cost is not None else vendoo.cost,
        sold_at=vendoo.sold_at or ledger.sold_at,
        days_listed=vendoo.days_listed if vendoo.days_listed is not None else ledger.days_listed,
        missing_sale_details=() if recorded else vendoo.missing_sale_details,
    )
    return rows


def _ledger_row(base: AnalyticsItem, line, listed_text: str, days_between) -> AnalyticsItem:
    """A sales-ledger line as a sold row, with the amounts the seller or marketplace recorded."""
    from vendoo_studio.services.sales_ledger import financial_values

    moment = line.sold_at or line.received_at
    sold_at = moment.replace(tzinfo=UTC) if moment and moment.tzinfo is None else moment
    data, stale = financial_values(line)
    # Analytics totals are in dollars; another currency is a sale with no amount here.
    data = data if data and not stale and data.get("currency") == "USD" else {}
    units = line.quantity if type(line.quantity) is int and line.quantity > 0 else 1
    revenue, refunds = _amount(data.get("item_revenue")), _amount(data.get("refunds"))
    item_cost = _amount(data.get("item_cost"))
    return replace(
        base,
        status="sold",
        order_backed=line.is_order_backed,
        sold_price=revenue,
        sold_at=sold_at,
        marketplace=line.marketplace,
        days_listed=days_between(listed_text, sold_at.isoformat()) if sold_at else None,
        fees=_amount(data.get("fees")),
        shipping_cost=_amount(data.get("shipping_cost")) or 0.0,
        shipping_credit=_amount(data.get("shipping_credit")) or 0.0,
        refunds=refunds or 0.0,
        cost=item_cost if item_cost is not None else (base.cost * units if base.cost is not None else None),
        sale_id=line.id,
    )


def summarize(
    items: list[AnalyticsItem],
    *,
    range_id: str,
    now: datetime,
    events: list[EventWindow] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    """Group already-loaded rows. ``now`` keeps the window testable."""
    window_start, window_end = _window(range_id, now, start, end)
    include_undated = range_id == "all"
    sales = [
        item for item in items
        if item.status == "sold"
        and _in_window(item, window_start, window_end, include_undated=include_undated)
    ]
    dated = [item for item in sales if item.sold_at is not None]
    buckets, truncated = _buckets(
        range_id, window_start, window_end, [item.sold_at for item in dated if item.sold_at],
    )
    period_sales: list[list[AnalyticsItem]] = [[] for _ in buckets]
    for item in dated:
        index = _place(item.sold_at, buckets) if item.sold_at is not None else None
        if index is not None:
            period_sales[index].append(item)

    previous = None
    if window_start is not None:
        span = window_end - window_start
        if range_id == "custom":
            span += timedelta(microseconds=1)
        previous_start = window_start - span
        previous_sales = [
            item for item in items
            if item.status == "sold" and item.sold_at is not None
            and previous_start <= item.sold_at < window_start
        ]
        previous = {
            "start": previous_start.isoformat(),
            "end": window_start.isoformat(),
            "sales": _sales_stats(previous_sales),
        }
    inventory = _inventory(items, now)
    available = len(sales) + inventory["active"]
    return {
        "range": range_id,
        "undated_sales": sum(1 for item in items if item.status == "sold" and item.sold_at is None),
        "incomplete_sales": _incomplete_sales(items),
        "periods_truncated": truncated,
        "inventory": inventory,
        # Snapshot-based rate: period sales plus inventory still available today.
        "sell_through_rate": round(len(sales) / available * 100, 1) if available else None,
        "sales": _sales_stats(sales),
        "previous": previous,
        "periods": [
            {"label": label, **_sales_stats(rows)}
            for (_, _, label), rows in zip(buckets, period_sales, strict=True)
        ],
        "marketplaces": _ranked_groups(_groups(sales, _marketplace_key)),
        "categories": _ranked_groups(_groups(sales, _category_key)),
        "brands": _ranked_groups(_groups(sales, _brand_key)),
        "labels": _label_groups(sales),
        "price_tiers": _price_tiers(sales),
        "aging": _aging(items, now),
        "stale": _stale(items, now),
        "stale_days": _stale_days(),
        "recent": _recent(sales, events or []),
        "oldest": _oldest(items, now),
    }


def _incomplete_sales(items: list[AnalyticsItem]) -> list[dict[str, Any]]:
    """What Vendoo's Missing Sale Details filter lists, across all time.

    The seller fixes these in Vendoo, so Studio holds a sale to Vendoo's rule
    rather than its own: a gap Vendoo does not flag is one the seller cannot clear.
    """
    rows = [
        {
            "conversation_id": item.conversation_id,
            "title": item.title,
            "missing": list(item.missing_sale_details),
        }
        for item in items
        if item.missing_sale_details
    ]
    return sorted(rows, key=lambda row: (row["title"].casefold(), row["conversation_id"]))


def price_tier(price: float) -> str:
    """The band a sale price falls in: the highest one whose floor it reaches."""
    return next(tier for tier, _label, floor in reversed(PRICE_TIERS) if price >= floor)


def _price_tiers(sales: list[AnalyticsItem]) -> list[dict[str, Any]]:
    """Every band, low to high, with the period's sales that fell in it, so the
    seller can see whether more of what sells is moving into the higher bands."""
    priced = [item for item in sales if item.sold_price is not None]
    return [
        {
            "id": tier,
            "label": label,
            **_sales_stats([item for item in priced if price_tier(item.sold_price or 0.0) == tier]),
        }
        for tier, label, _floor in PRICE_TIERS
    ]


def _sales_stats(sales: list[AnalyticsItem]) -> dict[str, Any]:
    priced = [item for item in sales if item.sold_price is not None]
    revenue = sum(item.sold_price or 0.0 for item in priced)
    profit_rows = [item for item in priced if item.cost is not None]
    days = [item.days_listed for item in sales if item.days_listed is not None]
    profit = sum(item.profit for item in profit_rows)
    known_revenue = sum(item.sold_price or 0.0 for item in profit_rows)
    return {
        "count": len(sales),
        "revenue": _money(revenue),
        "revenue_known": len(priced),
        "profit": _money(profit) if profit_rows else None,
        "profit_known": len(profit_rows),
        "fees_known": sum(1 for item in profit_rows if item.fees is not None),
        "average_price": _money(revenue / len(priced)) if priced else None,
        "median_days": _median_days(days),
        "days_known": len(days),
        "margin": round(profit / known_revenue * 100, 1) if known_revenue else None,
    }


def _effective_status(status: str, notes: dict) -> str:
    """Vendoo's label, unless Studio is mid-send or the last send failed."""
    if status in _BUSY:
        return status
    vendoo = str(notes.get("vendooStatus") or "")
    if vendoo in _VENDOO_STATUSES:
        return vendoo
    return status or "draft"


def _marketplace(sale: dict, dates: dict, notes: dict, aliases: dict[str, str]) -> str:
    raw = str(sale.get("marketplace") or "").strip()
    if not raw:
        sold_by = dates.get("soldByMarketplace")
        if isinstance(sold_by, dict) and sold_by:
            raw = max(sold_by, key=lambda market: str(sold_by.get(market) or ""))
    if not raw:
        listed = notes.get("vendooMarketplaces")
        if isinstance(listed, list) and len(listed) == 1:
            raw = str(listed[0] or "").strip()
    market = aliases.get(raw, raw)
    if not market or market in {"general", "validate"}:
        return "unknown"
    return market


def _category_label(value: Any) -> str:
    if isinstance(value, list):
        parts = [str(part).strip() for part in value if str(part).strip()]
        return parts[-1] if parts else ""
    text = str(value or "").strip()
    if text.startswith("["):
        import json
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return _category_label(parsed)
    if ">" in text:
        return text.split(">")[-1].strip()
    return text


def _inventory(items: list[AnalyticsItem], now: datetime) -> dict[str, Any]:
    """Stock by item, not by sale: one item with several sales is still one item.

    Analytics keeps a row per sale, and an item that still has stock is an
    active row beside its sale rows. The item's own row decides: a row that is
    not sold says how much stock is left, and an item whose every row is a sale
    is sold. This is the same count the Inventory tabs show.
    """
    state: dict[str, str] = {}
    stock: dict[str, AnalyticsItem] = {}
    for item in items:
        if not item.stock:
            continue
        stock.setdefault(item.conversation_id, item)
        if item.status != "sold":
            state[item.conversation_id] = item.status
    for conv_id, item in stock.items():
        state.setdefault(conv_id, item.status)
    counts: dict[str, float] = {"active": 0, "draft": 0, "sold": 0, "failed": 0, "working": 0}
    asking = 0.0
    for conv_id, status in state.items():
        item = stock[conv_id]
        if status == "active":
            counts["active"] += 1
            asking += item.price
        elif status == "sold":
            counts["sold"] += 1
        elif status == "failed":
            counts["failed"] += 1
        elif status in {"in_progress", "listing"}:
            counts["working"] += 1
        else:
            counts["draft"] += 1
    counts["asking_value"] = _money(asking)
    active = [stock[conv_id] for conv_id, status in state.items() if status == "active"]
    stale = [item for item in active if item.listed_at and (now - item.listed_at).days >= 90]
    counts["cost_value"] = _money(sum(item.cost or 0.0 for item in active if item.has_cost))
    counts["cost_known"] = sum(item.has_cost for item in active)
    counts["stale_count"] = len(stale)
    counts["stale_value"] = _money(sum(item.price for item in stale))
    counts["undated_count"] = sum(item.listed_at is None for item in active)
    return counts


def _groups(sales: list[AnalyticsItem], key_of):
    groups: dict[str, tuple[str, list[AnalyticsItem]]] = {}
    for item in sales:
        key, label = key_of(item)
        if not key:
            continue
        if key not in groups:
            groups[key] = (label, [])
        groups[key][1].append(item)
    return {
        key: {"id": key, "label": label, **_sales_stats(rows)}
        for key, (label, rows) in groups.items()
    }


def _marketplace_key(item: AnalyticsItem) -> tuple[str, str]:
    return item.marketplace, item.marketplace


def _category_key(item: AnalyticsItem) -> tuple[str, str]:
    label = item.category.strip()
    return label.casefold(), label


def _brand_key(item: AnalyticsItem) -> tuple[str, str]:
    label = item.brand.strip()
    return label.casefold(), label


def _label_groups(sales: list[AnalyticsItem]) -> list[dict[str, Any]]:
    """A sale counts once under each of its item's labels, with the color the seller gave the label."""
    from vendoo_studio.services.user_settings import LABEL_COLORS_KEY, get_ui_prefs

    colors = {label.casefold(): color for label, color in get_ui_prefs()[LABEL_COLORS_KEY].items()}
    groups: dict[str, tuple[str, list[AnalyticsItem]]] = {}
    for item in sales:
        keys = {label.casefold(): label for label in reversed(item.labels)}
        for key, label in keys.items():
            groups.setdefault(key, (label, []))[1].append(item)
    rows = _ranked_groups({
        key: {"id": key, "label": label, **_sales_stats(rows)}
        for key, (label, rows) in groups.items()
    })
    return [{**row, "color": colors.get(row["id"])} for row in rows]


def _ranked_groups(groups: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = sorted(
        groups.values(),
        key=lambda row: (-row["revenue"], -row["count"], str(row["label"]).lower()),
    )
    return [
        {**row, "revenue": _money(row["revenue"])}
        for row in rows
    ]


def _aging(items: list[AnalyticsItem], now: datetime) -> list[dict[str, Any]]:
    buckets = [
        {"label": label, "count": 0, "asking_value": 0.0, "listings": []}
        for label, _start, _end in _AGING
    ]
    undated = {"label": "No list date", "count": 0, "asking_value": 0.0, "listings": []}
    for item in items:
        if item.status != "active":
            continue
        days = None
        bucket = undated
        if item.listed_at is not None:
            days = max(0, int((now - item.listed_at).total_seconds() // 86_400))
            for index, (_label, start, end) in enumerate(_AGING):
                if days >= start and (end is None or days < end):
                    bucket = buckets[index]
                    break
        bucket["count"] += 1
        bucket["asking_value"] += item.price
        bucket["listings"].append({
            "conversation_id": item.conversation_id,
            "title": item.title,
            "price": _money(item.price),
            "days_listed": days,
        })
    return [
        {**row, "asking_value": _money(row["asking_value"]),
         "listings": sorted(row["listings"], key=lambda item: (-(item["days_listed"] or 0), item["title"]))}
        for row in [*buckets, undated] if row["count"]
    ]


def lowest_price(cost: float | None) -> int | None:
    """The whole-dollar price that still returns the item's cost after fees."""
    from vendoo_studio.services.scout import FEES

    if not cost or cost <= 0:
        return None
    return math.ceil(cost / (1 - FEES))


def deep_discount(price: float, floor: int | None) -> int | None:
    """The deepest cut that stays at or above ``floor``, or None when even the
    everyday event discount would sell this item at a loss."""
    if floor is None:
        return UNKNOWN_COST_PERCENT
    for percent in (*DEEP_PERCENTS, EVENT_PERCENT):
        if price * (1 - percent / 100) >= floor:
            return percent
    return None


def _stale_days() -> int:
    from vendoo_studio.services.user_settings import get_stale_listing_days

    return get_stale_listing_days()


def _stale(items: list[AnalyticsItem], now: datetime) -> list[dict[str, Any]]:
    """Active listings past the seller's stale threshold, oldest first, with how deep to cut."""
    stale_days = _stale_days()
    rows = []
    for item in items:
        if item.status != "active" or item.listed_at is None or item.price <= 0:
            continue
        days = int((now - item.listed_at).total_seconds() // 86_400)
        if days < stale_days:
            continue
        floor = lowest_price(item.cost)
        percent = deep_discount(item.price, floor)
        rows.append({
            "conversation_id": item.conversation_id,
            "title": item.title,
            "days_listed": days,
            "price": _money(item.price),
            "cost": _money(item.cost) if item.cost is not None else None,
            "lowest_price": floor,
            "discount_percent": percent,
            "sale_price": _money(item.price * (1 - percent / 100)) if percent is not None else None,
        })
    return sorted(rows, key=lambda row: (-row["days_listed"], row["title"]))


def _recent(sales: list[AnalyticsItem], events: list[EventWindow]) -> list[dict[str, Any]]:
    ordered = sorted(
        sales,
        key=lambda item: item.sold_at or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    return [
        {
            "conversation_id": item.conversation_id,
            "title": item.title,
            "price": _money(item.sold_price) if item.sold_price is not None else None,
            "marketplace": item.marketplace,
            "sold_at": item.sold_at.isoformat() if item.sold_at else None,
            "days_listed": item.days_listed,
            "event": next((event.name for event in events if event.covers(item)), None),
            "profit": _money(item.profit) if item.has_cost and item.sold_price is not None else None,
        }
        for item in ordered[:_RECENT]
    ]


def _oldest(items: list[AnalyticsItem], now: datetime) -> list[dict[str, Any]]:
    """Oldest active listings worth reviewing; never mutate or send them."""
    stale = [
        item for item in items
        if item.status == "active" and item.listed_at is not None
        and (now - item.listed_at).days >= 90
    ]
    stale.sort(key=lambda item: (item.listed_at, -item.price, item.conversation_id))
    return [{
        "conversation_id": item.conversation_id,
        "title": item.title,
        "price": _money(item.price),
        "days_listed": (now - item.listed_at).days,
    } for item in stale[:_RECENT] if item.listed_at is not None]


def _window(
    range_id: str, now: datetime, start: date | None, end: date | None,
) -> tuple[datetime | None, datetime]:
    """The sales window. A custom range ends at the close of its last day, or now if sooner."""
    if range_id != "custom":
        return _window_start(range_id, now), now
    if start is None or end is None:
        raise ValueError("custom range needs a start and an end")
    first = datetime(start.year, start.month, start.day, tzinfo=UTC)
    last = datetime(end.year, end.month, end.day, tzinfo=UTC) + timedelta(days=1)
    return first, min(last - timedelta(microseconds=1), now)


def _window_start(range_id: str, now: datetime) -> datetime | None:
    """Start of the range. Calendar ranges start at UTC midnight (weeks on Monday)."""
    today = datetime(now.year, now.month, now.day, tzinfo=UTC)
    if range_id == "wtd":
        return today - timedelta(days=today.weekday())
    if range_id == "mtd":
        return today.replace(day=1)
    if range_id == "ytd":
        return today.replace(month=1, day=1)
    if range_id == "12m":
        year, month = _add_months(now.year, now.month, -11)
        return datetime(year, month, 1, tzinfo=UTC)
    days = {"7d": 7, "30d": 30, "90d": 90}.get(range_id)
    if days is None:
        return None
    return now - timedelta(days=days)


def _in_window(
    item: AnalyticsItem, start: datetime | None, end: datetime, *, include_undated: bool,
) -> bool:
    if item.sold_at is None:
        return include_undated
    return item.sold_at <= end and (start is None or item.sold_at >= start)


def _buckets(
    range_id: str,
    start: datetime | None,
    now: datetime,
    dated: list[datetime],
) -> tuple[list[tuple[datetime, datetime, str]], bool]:
    if range_id in ("wtd", "mtd"):
        assert start is not None
        return _day_buckets(start, now), False
    if range_id == "custom":
        assert start is not None
        if (now - start).days < _MAX_DAILY_DAYS:
            return _day_buckets(start, now), False
        count = (now.year * 12 + now.month) - (start.year * 12 + start.month) + 1
        return _month_buckets(now, min(count, _MAX_MONTHS)), count > _MAX_MONTHS
    if range_id == "ytd":
        return _month_buckets(now, now.month), False
    if range_id == "7d":
        return _span_buckets(now, days=7, count=7), False
    if range_id == "30d":
        return _span_buckets(now, days=30, count=6), False
    if range_id == "90d":
        return _span_buckets(now, days=90, count=6), False
    if range_id == "12m":
        return _month_buckets(now, 12), False
    if not dated:
        return [], False
    earliest = min(dated)
    start_index = earliest.year * 12 + (earliest.month - 1)
    end_index = now.year * 12 + (now.month - 1)
    count = max(1, end_index - start_index + 1)
    truncated = count > _MAX_MONTHS
    return _month_buckets(now, min(count, _MAX_MONTHS)), truncated


def _span_buckets(now: datetime, *, days: int, count: int):
    start = now - timedelta(days=days)
    step = (now - start) / count
    buckets = []
    for index in range(count):
        bucket_start = start + step * index
        bucket_end = now if index == count - 1 else start + step * (index + 1)
        buckets.append((bucket_start, bucket_end, f"{bucket_start.month}/{bucket_start.day}"))
    return buckets


def _day_buckets(start: datetime, now: datetime):
    buckets = []
    day = start
    while day <= now:
        buckets.append((day, day + timedelta(days=1), f"{day.month}/{day.day}"))
        day += timedelta(days=1)
    return buckets


def _month_buckets(now: datetime, count: int):
    buckets = []
    for offset in range(count - 1, -1, -1):
        year, month = _add_months(now.year, now.month, -offset)
        start = datetime(year, month, 1, tzinfo=UTC)
        next_year, next_month = _add_months(year, month, 1)
        end = datetime(next_year, next_month, 1, tzinfo=UTC)
        label = _MONTHS[month - 1]
        if year != now.year:
            label = f"{label} '{year % 100:02d}"
        buckets.append((start, end, label))
    return buckets


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _place(moment: datetime, buckets) -> int | None:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    for index, (start, end, _label) in enumerate(buckets):
        last = index == len(buckets) - 1
        if moment >= start and (moment < end or (last and moment <= end)):
            return index
    return None


def _median_days(values: list[int]) -> int | None:
    if not values:
        return None
    return int(round(statistics.median(values)))


def _number(value: Any) -> float:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(amount) or amount <= 0:
        return 0.0
    return amount


def _amount(value: Any) -> float | None:
    """A recorded sale amount, where zero is a real figure and absent is None."""
    if value is None or value == "":
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(amount) or amount < 0:
        return None
    return amount


def _money(value: float) -> float:
    return round(float(value), 2)
