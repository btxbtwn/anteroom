"""The Sourcing tab's boxes, as the business assistant reads them.

The tab draws every box in the last sourcing check (``box_scout``'s snapshot):
the buy list, each supplier's top picks, and every other box with what kept it
off the list. This reads that same snapshot, so the assistant sees the same
boxes with the same figures. Profit and ROI are the ones the box-scout skill's
``price_lot`` worked out in that check, and a box's standing is the skill's own
``standalone_check`` and buy-list exclusion; nothing here prices a box again.

Read fresh on every question: a refresh rewrites the snapshot, and the next
question reads the new one.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from vendoo_studio.services import box_scout
from vendoo_studio.services.inventory_analytics import AnalyticsItem, _sales_stats

# A check older than two refresh intervals means the timer has not been able to run one.
STALE_AFTER = 2 * box_scout.REFRESH_INTERVAL
RECOMMENDED_LOTS = 40

# One box, as the lookup table holds it.
BOX_COLUMNS = (
    "id", "store", "title", "url", "contents", "grade", "vip", "price", "shipping", "checkout_fee", "landed",
    "pieces", "pieces_estimated", "usable_pieces", "resale_per_piece", "resale_low", "sell_through",
    "expected_revenue", "projected_profit", "roi_percent", "min_roi_percent", "passes_min_roi", "roi_gap_points",
    "profit_shortfall", "downside_profit", "on_its_own", "buy_list", "top_pick", "comps_count", "price_source",
    "priced_at", "store_listed_on", "checked_at", "kind_sold_12m", "kind_average_price", "kind_margin",
    "kind_median_days",
)
# The same reasons the tab's "Other available boxes" rows give (`boxScout.ts`).
BUY_LIST_REASONS = {
    "recommended": "on the buy list",
    "needs_research": "need recent sold evidence",
    "same_theme": "are another box of a kind already picked",
    "theme_stocked": "are a kind the seller already holds plenty of",
    "return_target": "are below the min ROI",
    "downside": "lose money in the lower-sales test",
    "budget": "don't fit the budget",
    "alternative": "lost to another combination",
}


def box_rows(state: dict, sales: list[AnalyticsItem], now: datetime) -> list[dict[str, Any]]:
    """Every box the Sourcing tab shows, best first, with where each stands.

    ``sales`` are the seller's rows from ``inventory_analytics.load_rows``; the
    ``kind_*`` columns are their last 12 months of sales of the kind of piece
    a box holds, matched the way the buy list matches its own sales.
    """
    snapshot = state.get("snapshot")
    if not snapshot:
        return []
    s = box_scout.scout()
    prefs = snapshot["preferences"]
    plan = snapshot["buy_list"]
    in_cart = {_key(lot): lot for cart in plan["carts"] for lot in cart["lots"]}
    picks = {_key(pick) for store_picks in snapshot["store_picks"].values() for pick in store_picks}
    kinds = kind_sales(sales, {lot["theme"] for lot in snapshot["lots"]}, now)
    rows = []
    for listed in snapshot["lots"]:
        key = _key(listed)
        # A recommended box is shown with its cart's figures, free shipping included.
        lot = in_cart.get(key, listed)
        roi = lot["roi"]
        passes = None if roi is None else roi >= prefs["min_roi"]
        missed = passes is False
        kind = kinds.get(lot["theme"]) or {}
        rows.append({
            "id": key,
            "store": snapshot["stores"].get(lot["store"], {}).get("name", lot["store"]),
            "title": lot["title"],
            "url": lot["url"],
            "contents": lot["theme"],
            "grade": lot.get("grade"),
            "vip": lot.get("vip"),
            "price": lot["price"],
            "shipping": lot["ship_est"],
            "checkout_fee": lot.get("checkout_fee"),
            "landed": lot["landed"],
            "pieces": lot["pcs"],
            "pieces_estimated": lot.get("pcs_estimated"),
            "usable_pieces": lot.get("usable_pcs"),
            "resale_per_piece": lot["resale_per_pc"],
            "resale_low": lot.get("resale_low"),
            "sell_through": lot["sell_through"],
            "expected_revenue": lot["expected_revenue"],
            "projected_profit": lot["expected_profit"],
            "roi_percent": _percent(roi),
            "min_roi_percent": _percent(prefs["min_roi"]),
            "passes_min_roi": passes,
            "roi_gap_points": round((prefs["min_roi"] - roi) * 100, 1) if missed else None,
            "profit_shortfall": round(prefs["min_roi"] * lot["landed"] - lot["expected_profit"], 2) if missed else None,
            "downside_profit": lot.get("downside_profit"),
            "on_its_own": s.standalone_check(listed, budget=prefs["budget"], min_roi=prefs["min_roi"]),
            "buy_list": "recommended" if key in in_cart else plan["exclusions"].get(key),
            "top_pick": key in picks,
            "comps_count": lot.get("comps_count"),
            "price_source": lot.get("research_source"),
            "priced_at": lot.get("research_at"),
            "store_listed_on": lot.get("listed"),
            "checked_at": snapshot["updated_at"],
            "kind_sold_12m": kind.get("count", 0),
            "kind_average_price": kind.get("average_price"),
            "kind_margin": kind.get("margin"),
            "kind_median_days": kind.get("median_days"),
        })
    order = {"recommended": 0}
    rows.sort(key=lambda row: (order.get(row["buy_list"], 1), not row["top_pick"], row["roi_percent"] is None,
                               -(row["roi_percent"] or 0)))
    return rows


def kind_sales(sales: list[AnalyticsItem], themes: set[str], now: datetime) -> dict[str, dict[str, Any]]:
    """Per kind of piece, the seller's sales of it in the last 12 months, in Analytics' figures."""
    s = box_scout.scout()
    since = now - box_scout.MEASURED_WINDOW
    recent = [item for item in sales if item.status == "sold" and item.sold_at is not None
              and _aware(item.sold_at) >= since]
    parts = [(item, *s.theme_parts(item.title)) for item in recent]
    found = {}
    for theme in themes:
        matching = [item for item, styles, garment in parts if s.theme_matches(theme, styles, garment)]
        if matching:
            found[theme] = _sales_stats(matching)
    return found


def buy_list_summary(state: dict, rows: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """The buy list the tab recommends, and why it is empty when it is."""
    snapshot = state.get("snapshot")
    if not snapshot:
        return {"recommended_lots": [], "empty_reason": (
            "No sourcing check has finished yet, so there are no boxes to recommend. "
            "The Sourcing tab runs one when it is opened.")}
    plan = snapshot["buy_list"]
    prefs = snapshot["preferences"]
    by_id = {row["id"]: row for row in rows}
    recommended = [by_id[_key(lot)] for cart in plan["carts"] for lot in cart["lots"] if _key(lot) in by_id]
    reasons = Counter(row["buy_list"] for row in rows)
    priced = [row for row in rows if row["roi_percent"] is not None]
    summary: dict[str, Any] = {
        "checked_at": snapshot["updated_at"],
        "settings_used": prefs,
        "budget": plan["budget"],
        "total": plan["total"],
        "expected_profit": plan["expected_profit"],
        "recommended_lots": [_brief(row) for row in recommended][:RECOMMENDED_LOTS],
        "boxes_on_tab": len(rows),
        "boxes_priced": len(priced),
        "boxes_reaching_min_roi": sum(1 for row in rows if row["passes_min_roi"]),
        "boxes_by_store": dict(Counter(row["store"] for row in rows)),
        "buy_list_standing": {BUY_LIST_REASONS.get(reason, reason): count for reason, count in reasons.most_common()},
    }
    if not recommended:
        summary["empty_reason"] = _empty_reason(snapshot, rows, priced)
    if note := _freshness(state, now):
        summary["freshness"] = note
    return summary


def _empty_reason(snapshot: dict, rows: list[dict[str, Any]], priced: list[dict[str, Any]]) -> str:
    prefs = snapshot["preferences"]
    target = _percent(prefs["min_roi"])
    if not rows:
        errors = [store["error"] for store in snapshot["stores"].values() if store.get("error")]
        return "No boxes found in the last check." + (f" {' '.join(errors)}" if errors else "")
    if not priced:
        return (f"0 of {len(rows)} boxes can be priced: none has at least 3 recent comparable sales, "
                "so none has a projected profit or ROI.")
    counts = Counter(row["buy_list"] for row in rows)
    standing = ", ".join(f"{count} {BUY_LIST_REASONS.get(reason, reason)}" for reason, count in counts.most_common())
    best = max(priced, key=lambda row: row["roi_percent"])
    reaching = [row for row in rows if row["passes_min_roi"]]
    best_text = f"{best['roi_percent']:g}% ({best['title']}, {best['store']})"
    lead = (f"{len(reaching)} of {len(rows)} boxes reach the {target:g}% min ROI (best is {best_text}), "
            "but none of them passes every other check" if reaching
            else f"0 of {len(rows)} boxes reach the {target:g}% min ROI (best is {best_text})")
    return f"{lead}, within the ${prefs['budget']:g} budget: {standing}."


def _freshness(state: dict, now: datetime) -> str | None:
    snapshot = state["snapshot"]
    notes = []
    try:
        age = now - datetime.fromisoformat(snapshot["updated_at"])
    except (TypeError, ValueError):
        age = None
    if age is None or age >= STALE_AFTER:
        days = math.floor(age / timedelta(days=1)) if age is not None else None
        notes.append(f"The box data is {f'{days} days' if days else 'more than 12 hours'} old: "
                     "refresh the buy list on the Sourcing tab before buying.")
    changed = [key for key in box_scout.PLAN_PREFS if snapshot["preferences"].get(key) != state["prefs"].get(key)]
    if changed:
        notes.append(f"The seller changed {', '.join(name.replace('_', ' ') for name in changed)} since this check; "
                     "these figures use the earlier settings until the new check finishes.")
    return " ".join(notes) or None


# What the brief shows of a recommended box; the lookup table has the rest.
_BRIEF_KEYS = ("id", "store", "title", "contents", "price", "shipping", "landed", "pieces", "resale_per_piece",
               "projected_profit", "roi_percent", "downside_profit", "comps_count")


def _brief(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in _BRIEF_KEYS}


def _key(lot: dict) -> str:
    return f"{lot['store']}:{lot['variant_id']}"


def _percent(fraction: float | None) -> float | None:
    return None if fraction is None else round(fraction * 100, 1)


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
