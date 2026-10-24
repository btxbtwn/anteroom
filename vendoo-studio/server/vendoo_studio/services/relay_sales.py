"""Turn relay events into sales the seller can act on.

The relay sends IDs only. An eBay ORDER_CONFIRMATION names the order and each
line's listing ID; an Etsy order.paid names the receipt, whose lines Studio
reads through the relay's Etsy forwarding. Each line becomes one
MarketplaceSale, matched to the Studio listing whose Vendoo-reported URL on
that marketplace carries the same listing ID. Nothing here delists: a sale
waits for the seller.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.services.vendoo_import import parse_notes

log = logging.getLogger("vendoo_studio.relay_sales")

# Listing IDs in the URLs Vendoo reports for each marketplace.
LISTING_URL_ID = {
    "ebay": re.compile(r"ebay\.[a-z.]+/itm/(?:[^/?#]+/)?(\d{6,})", re.I),
    "etsy": re.compile(r"etsy\.com/(?:[a-z-]+/)?listing/(\d+)", re.I),
}


def listing_id_from_url(marketplace: str, url: str | None) -> str | None:
    pattern = LISTING_URL_ID.get(marketplace)
    match = pattern.search(url or "") if pattern else None
    return match.group(1) if match else None


def find_listing(db: Session, marketplace: str, listing_id: str | None) -> Conversation | None:
    """The Studio listing whose Vendoo-reported URL on ``marketplace`` has this ID."""
    if not listing_id:
        return None
    rows = db.query(Conversation).filter(Conversation.notes.contains(listing_id)).all()
    for conv in rows:
        urls = parse_notes(conv.notes).get("vendooListingUrls") or {}
        if isinstance(urls, dict) and listing_id_from_url(marketplace, urls.get(marketplace)) == listing_id:
            return conv
    return None


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, UTC).replace(tzinfo=None)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def record_sale(
    db: Session,
    *,
    marketplace: str,
    order_id: str,
    line_id: str,
    listing_id: str | None,
    quantity: int | None,
    sold_at: datetime | None,
) -> MarketplaceSale | None:
    """Store one sold line, once. Returns the new row, or None if already known."""
    exists = (
        db.query(MarketplaceSale.id)
        .filter_by(marketplace=marketplace, order_id=order_id, line_id=line_id)
        .first()
    )
    if exists:
        return None
    conv = find_listing(db, marketplace, listing_id)
    sale = MarketplaceSale(
        marketplace=marketplace,
        order_id=order_id,
        line_id=line_id,
        listing_id=listing_id,
        quantity=quantity,
        conversation_id=conv.id if conv else None,
        sold_at=sold_at,
    )
    db.add(sale)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    log.info(
        "%s sale %s/%s recorded%s", marketplace, order_id, line_id,
        f" for listing {conv.id}" if conv else " (no matching listing)",
    )
    return sale


def _first(ids: dict, key: str) -> str | None:
    values = ids.get(key) or []
    return str(values[0]) if values else None


def ebay_lines(event: dict) -> list[dict]:
    """eBay sends one notification per line, but each names all of the order's lines.

    The relay keeps IDs as flat lists, so lines pair up by position.
    """
    ids = event.get("ids") or {}
    order_id = _first(ids, "orderId")
    if not order_id:
        return []
    listing_ids = [str(v) for v in ids.get("listingId") or []]
    line_ids = [str(v) for v in ids.get("orderLineItemId") or []]
    if not line_ids:
        line_ids = listing_ids or [order_id]
    return [
        {
            "order_id": order_id,
            "line_id": line_id,
            "listing_id": listing_ids[i] if i < len(listing_ids) else None,
        }
        for i, line_id in enumerate(line_ids)
    ]


EtsyReceiptReader = Callable[[str, str], Awaitable[dict]]


async def etsy_lines(event: dict, read_receipt: EtsyReceiptReader) -> list[dict]:
    ids = event.get("ids") or {}
    shop_id, receipt_id = _first(ids, "shop_id"), _first(ids, "receipt_id")
    if not shop_id or not receipt_id:
        return []
    receipt = await read_receipt(shop_id, receipt_id)
    lines = []
    for txn in receipt.get("transactions") or []:
        listing_id = txn.get("listing_id")
        transaction_id = txn.get("transaction_id")
        if listing_id is None and transaction_id is None:
            continue
        lines.append({
            "order_id": receipt_id,
            "line_id": str(transaction_id if transaction_id is not None else listing_id),
            "listing_id": str(listing_id) if listing_id is not None else None,
            "quantity": txn.get("quantity"),
            "sold_at": _parse_time(txn.get("paid_timestamp") or receipt.get("created_timestamp")),
        })
    return lines


SALE_EVENTS = {("ebay", "ORDER_CONFIRMATION"), ("etsy", "order.paid")}


async def handle_event(
    event: dict,
    *,
    session_factory: Callable[[], Session],
    read_etsy_receipt: EtsyReceiptReader,
) -> list[str]:
    """Record the sales in one relay event. Returns the new sale IDs.

    Other events (listing changes, account deletion) are acknowledged and
    left alone for now.
    """
    source, kind = event.get("source"), event.get("eventType")
    if (source, kind) not in SALE_EVENTS:
        if (source, kind) == ("ebay", "MARKETPLACE_ACCOUNT_DELETION"):
            from vendoo_studio.services.marketplace_accounts import forget_deleted_ebay_account

            forget_deleted_ebay_account(_first(event.get("ids") or {}, "userId"))
        return []
    if source == "ebay":
        lines = ebay_lines(event)
        sold_at = _parse_time(event.get("occurredAt"))
        for line in lines:
            line.setdefault("sold_at", sold_at)
    else:
        lines = await etsy_lines(event, read_etsy_receipt)
    created = []
    with session_factory() as db:
        for line in lines:
            sale = record_sale(
                db,
                marketplace=source,
                order_id=line["order_id"],
                line_id=line["line_id"],
                listing_id=line.get("listing_id"),
                quantity=line.get("quantity"),
                sold_at=line.get("sold_at"),
            )
            if sale:
                created.append(sale.id)
    return created
