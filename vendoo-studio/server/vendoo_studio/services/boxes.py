"""Wholesale boxes the seller bought, and what came of them.

A box is recorded when it is bought, usually straight from a Sourcing buy-list
row, and the listings made from it point at it. That is enough to answer the
question the buy list exists for: was the box worth it? Money in is the box
price plus its shipping; money back is what each sale brought in after fees and
shipping, read from the same rows Analytics uses.

An item with no cost of goods of its own carries the box's cost divided by the
pieces worth listing, both in Analytics and on the Vendoo form: what the seller
paid for the pieces that turned out unsellable is part of what the rest cost.

Each box and store is also scored the way a reseller grades a supplier: the
average sale price, how many sales reached the premium band, how much of the
box was not worth listing, and how much of it sold within 30, 60 and 90 days of
buying it.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.sourcing import SourceBox
from vendoo_studio.services.inventory_analytics import PREMIUM_PRICE, AnalyticsItem, load_rows

_LISTED = {"active", "sold"}
# Sales from bought boxes before a store's resale estimates are adjusted, and
# how far they may move: a few lucky or unlucky sales should not swing the list.
CALIBRATION_MIN_SALES = 5
CALIBRATION_RANGE = (0.3, 2.0)
# Orders from a store before its shipping estimates follow what it charged,
# and how far they may move from the carrier's list rate.
SHIPPING_MIN_ORDERS = 2
SHIPPING_RANGE = (0.2, 1.5)
# Days after buying a box at which its sell-through is read, once it is that old.
SELL_THROUGH_DAYS = (30, 60, 90)


class BoxCountError(ValueError):
    """More pieces marked unsellable than the box held."""


def list_boxes(db: Session) -> list[SourceBox]:
    return db.query(SourceBox).order_by(SourceBox.bought_at.desc(), SourceBox.created_at.desc()).all()


def get_box(db: Session, box_id: str) -> SourceBox | None:
    return db.get(SourceBox, box_id)


def create_box(
    db: Session,
    *,
    store: str,
    title: str,
    price: float,
    shipping: float = 0.0,
    pieces: int | None = None,
    url: str | None = None,
    bought_at: datetime | None = None,
    estimate_per_piece: float | None = None,
    list_shipping: float | None = None,
    unsellable: int | None = None,
) -> SourceBox:
    box = SourceBox(
        store=store.strip(),
        title=title.strip(),
        price=price,
        shipping=shipping,
        pieces=pieces or None,
        unsellable=unsellable,
        estimate_per_piece=estimate_per_piece or None,
        list_shipping=list_shipping or None,
        url=(url or "").strip() or None,
        bought_at=bought_at or datetime.now(UTC),
    )
    _check_counts(box)
    db.add(box)
    db.commit()
    db.refresh(box)
    return box


def update_box(db: Session, box: SourceBox, changes: dict[str, Any]) -> SourceBox:
    for key, value in changes.items():
        if key in {"store", "title"} and isinstance(value, str):
            value = value.strip()
        if key == "url":
            value = (value or "").strip() or None
        if key == "pieces":
            value = value or None
        setattr(box, key, value)
    try:
        _check_counts(box)
    except BoxCountError:
        db.rollback()
        raise
    db.commit()
    db.refresh(box)
    return box


def delete_box(db: Session, box: SourceBox) -> None:
    """Forget the box. Its listings stay; they just no longer belong to a box."""
    db.query(Conversation).filter(Conversation.box_id == box.id).update({Conversation.box_id: None})
    db.delete(box)
    db.commit()


def assign_box(db: Session, conv: Conversation, box_id: str | None) -> None:
    if box_id and get_box(db, box_id) is None:
        raise LookupError(box_id)
    conv.box_id = box_id or None


def cost_share(box: SourceBox | None) -> float | None:
    """One sellable piece's share of what the box cost, delivered."""
    if box is None or not box.sellable:
        return None
    return round(box.landed / box.sellable, 2)


def _check_counts(box: SourceBox) -> None:
    if box.unsellable and box.pieces and box.unsellable > box.pieces:
        raise BoxCountError(f"{box.unsellable} unsellable is more than the {box.pieces} pieces in the box")


def results(db: Session, now: datetime | None = None) -> dict[str, Any]:
    """Every box with what it has returned so far, and the same per store."""
    now = now or datetime.now(UTC)
    boxes = list_boxes(db)
    items_by_box: dict[str, list[AnalyticsItem]] = {}
    for item in load_rows(db):
        if item.box_id:
            items_by_box.setdefault(item.box_id, []).append(item)
    rows = [_box_row(box, items_by_box.get(box.id, []), now) for box in boxes]
    stores: dict[str, list[tuple[SourceBox, list[AnalyticsItem]]]] = {}
    for box in boxes:
        stores.setdefault(box.store, []).append((box, items_by_box.get(box.id, [])))
    return {
        "boxes": rows,
        "stores": [_store_row(name, entries, now) for name, entries in sorted(stores.items())],
    }


def resale_calibration(db: Session) -> dict[str, dict[str, Any]]:
    """Per store, what pieces from bought boxes sold for against the estimate the
    buy list used when the box was bought. The median ratio becomes the store's
    factor once there are enough sales to trust it."""
    estimates = {
        box.id: (box.store, box.estimate_per_piece)
        for box in list_boxes(db)
        if box.estimate_per_piece
    }
    ratios: dict[str, list[float]] = {}
    for item in load_rows(db):
        if item.status != "sold" or not item.sold_price or item.box_id not in estimates:
            continue
        store, estimate = estimates[item.box_id]
        ratios.setdefault(store, []).append(item.sold_price / estimate)
    low, high = CALIBRATION_RANGE
    return {
        store: {
            "factor": (
                round(min(high, max(low, statistics.median(values))), 2)
                if len(values) >= CALIBRATION_MIN_SALES else None
            ),
            "sales": len(values),
            "needed": CALIBRATION_MIN_SALES,
        }
        for store, values in ratios.items()
    }


def shipping_calibration(db: Session) -> dict[str, dict[str, Any]]:
    """Per store, what its orders were charged for shipping against the carrier
    list rate the buy list estimated from. The median ratio becomes the store's
    shipping factor once enough orders are recorded."""
    ratios: dict[str, list[float]] = {}
    for box in list_boxes(db):
        if box.list_shipping and box.shipping > 0:
            ratios.setdefault(box.store, []).append(box.shipping / box.list_shipping)
    low, high = SHIPPING_RANGE
    return {
        store: {
            "factor": (
                round(min(high, max(low, statistics.median(values))), 4)
                if len(values) >= SHIPPING_MIN_ORDERS else None
            ),
            "orders": len(values),
            "needed": SHIPPING_MIN_ORDERS,
        }
        for store, values in ratios.items()
    }


def _box_row(box: SourceBox, items: list[AnalyticsItem], now: datetime) -> dict[str, Any]:
    return {
        "id": box.id,
        "store": box.store,
        "title": box.title,
        "url": box.url,
        "price": round(box.price, 2),
        "shipping": round(box.shipping, 2),
        "pieces": box.pieces,
        "sellable": box.sellable,
        "estimate_per_piece": box.estimate_per_piece,
        "list_shipping": box.list_shipping,
        "bought_at": _iso(box.bought_at),
        "cost_per_piece": cost_share(box),
        **_totals([(box, items)], now),
    }


def _store_row(store: str, entries: list[tuple[SourceBox, list[AnalyticsItem]]], now: datetime) -> dict[str, Any]:
    return {"store": store, "boxes": len(entries), **_totals(entries, now)}


def _totals(entries: list[tuple[SourceBox, list[AnalyticsItem]]], now: datetime) -> dict[str, Any]:
    spent = sum(box.landed for box, _items in entries)
    sellable = _sellable(entries)
    items = [item for _box, box_items in entries for item in box_items]
    sold = [item for item in items if item.status == "sold"]
    priced = [item.sold_price for item in sold if item.sold_price is not None]
    returned = sum(item.net for item in sold)
    days = [item.days_listed for item in sold if item.days_listed is not None]
    # Only boxes where the seller recorded how many were unsellable, zero included.
    counted = [box for box, _items in entries if box.unsellable is not None and box.pieces]
    unsellable = sum(box.unsellable for box in counted) if counted else None
    return {
        "spent": round(spent, 2),
        "listings": len(items),
        "listed": sum(1 for item in items if item.status in _LISTED),
        "sold": len(sold),
        "returned": round(returned, 2),
        "profit": round(returned - spent, 2),
        "roi": round((returned - spent) / spent, 3) if spent > 0 else None,
        # Of the sellable pieces when the counts are known, else of the listings made.
        "sell_through": round(len(sold) / (sellable or len(items)), 3) if (sellable or items) else None,
        "sell_through_by_age": _sell_through_by_age(entries, now),
        "unsold_asking": round(sum(item.price for item in items if item.status == "active"), 2),
        "median_days": int(statistics.median(days)) if days else None,
        "asp": round(sum(priced) / len(priced), 2) if priced else None,
        "sold_premium": sum(1 for price in priced if price >= PREMIUM_PRICE),
        "unsellable": unsellable,
        "unsellable_rate": (
            round(unsellable / sum(box.pieces for box in counted), 3) if unsellable is not None else None
        ),
    }


def _sellable(entries: list[tuple[SourceBox, list[AnalyticsItem]]]) -> int | None:
    """Sellable pieces across the boxes, when every box's count is known."""
    counts = [box.sellable for box, _items in entries]
    return sum(counts) if counts and all(count is not None for count in counts) else None


def _sell_through_by_age(entries: list[tuple[SourceBox, list[AnalyticsItem]]], now: datetime) -> list[dict[str, Any]]:
    """Share of the pieces sold within 30, 60 and 90 days of buying, counting
    only boxes bought at least that long ago, so a new box does not drag the
    rate down before it has had the time."""
    rows = []
    for window in SELL_THROUGH_DAYS:
        span = timedelta(days=window)
        old_enough = [(box, items) for box, items in entries if _aware(box.bought_at) <= now - span]
        if not old_enough:
            continue
        of = _sellable(old_enough) or sum(len(items) for _box, items in old_enough)
        sold = sum(
            1
            for box, items in old_enough
            for item in items
            if item.status == "sold" and item.sold_at is not None
            and _aware(item.sold_at) <= _aware(box.bought_at) + span
        )
        rows.append({"days": window, "sold": sold, "of": of, "rate": round(sold / of, 3) if of else None})
    return rows


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()
