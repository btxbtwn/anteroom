"""Turn relay events into sales the seller can act on.

The relay sends IDs only. An eBay ORDER_CONFIRMATION names an order which is
read through the same verified order reader as catch-up; an Etsy order.paid
names the receipt, whose lines Studio reads through the relay's Etsy forwarding. Each line becomes one
MarketplaceSale, matched to the Studio listing whose Vendoo-reported URL on
that marketplace carries the same listing ID. Nothing here delists: a sale
waits for the seller.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.marketplace_listing import MarketplaceListing
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.services.marketplace_identity import listing_id as listing_id_from_url
from vendoo_studio.services.sale_matching import replace_stand_in
from vendoo_studio.services.vendoo_import import parse_notes

log = logging.getLogger("vendoo_studio.relay_sales")

def matching_listings(db: Session, marketplace: str, listing_id: str | None) -> list[Conversation]:
    """Exact identity matches across direct records, saved imports, and seller links."""
    if not listing_id:
        return []
    if marketplace == "poshmark":
        listing_id = listing_id.lower()
    matches: dict[str, Conversation] = {}
    direct = db.query(MarketplaceListing).filter(MarketplaceListing.marketplace == marketplace,
                                                 MarketplaceListing.listing_url.contains(listing_id)).all()
    for row in direct:
        if listing_id_from_url(marketplace, row.listing_url) == listing_id:
            conv = db.get(Conversation, row.conversation_id)
            if conv:
                matches[conv.id] = conv
    rows = db.query(Conversation).filter(Conversation.notes.contains(listing_id)).all()
    for conv in rows:
        notes = parse_notes(conv.notes)
        urls = notes.get("vendooListingUrls") or {}
        marks = notes.get("marketplaceMarks") or {}
        mark = marks.get(marketplace) if isinstance(marks, dict) else None
        linked = [urls.get(marketplace) if isinstance(urls, dict) else None,
                  mark.get("url") if isinstance(mark, dict) and mark.get("state") == "listed" else None]
        if any(listing_id_from_url(marketplace, url) == listing_id for url in linked):
            matches[conv.id] = conv
    return list(matches.values())


def find_listing(db: Session, marketplace: str, listing_id: str | None) -> Conversation | None:
    """Assign a sale only when exactly one local item owns its listing reference."""
    matches = matching_listings(db, marketplace, listing_id)
    if len(matches) > 1:
        log.warning("%s sale has ambiguous listing ownership across %s items; left unmatched", marketplace, len(matches))
    return matches[0] if len(matches) == 1 else None


def reconcile_unmatched(db: Session, *, conversation_id: str | None = None, marketplace: str | None = None) -> int:
    """Match previously received sales to exact listing links, without remote actions.

    Dismissed sales and existing assignments are left alone. Duplicate local
    owners remain unresolved. A sale linked here replaces the Vendoo copy or
    Mark as sold that stood for it. Nothing here notifies, publishes, or delists.
    """
    pending = db.query(MarketplaceSale.id, MarketplaceSale.marketplace, MarketplaceSale.listing_id).filter(
        MarketplaceSale.state == "new", MarketplaceSale.conversation_id.is_(None), MarketplaceSale.listing_id.isnot(None),
    )
    if marketplace:
        pending = pending.filter(MarketplaceSale.marketplace == marketplace)
    owners: dict[tuple[str, str | None], str | None] = {}
    linked: list[str] = []
    for sale_id, marketplace, item_id in pending.yield_per(100):
        key = (marketplace, item_id)
        if key not in owners:
            owner = find_listing(db, marketplace, item_id)
            owners[key] = owner.id if owner else None
        owner_id = owners[key]
        if not owner_id or (conversation_id and owner_id != conversation_id):
            continue
        # Re-check assignment and state when writing, so an overlapping refresh
        # cannot take a sale another request just assigned or dismissed.
        if db.query(MarketplaceSale).filter(
            MarketplaceSale.id == sale_id, MarketplaceSale.conversation_id.is_(None), MarketplaceSale.state == "new",
        ).update({"conversation_id": owner_id}, synchronize_session=False):
            linked.append(sale_id)
    for sale_id in linked:
        sale = db.get(MarketplaceSale, sale_id, populate_existing=True)
        if sale is not None:
            replace_stand_in(db, sale)
    db.commit()
    return len(linked)


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, UTC).replace(tzinfo=None)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def find_sale_line(db: Session, marketplace: str, order_id: str, line_id: str,
                   listing_id: str | None) -> MarketplaceSale | None:
    """Match exact line IDs or an eBay item/transaction pair, never a listing alone.

    Trading supplies ItemID-TransactionID; some relay events supply only the
    transaction suffix. An alias is accepted only against an existing row with
    the same order and exact listing ID. Keep the existing row and its money.
    """
    query = db.query(MarketplaceSale).filter_by(marketplace=marketplace, order_id=order_id)
    if marketplace == 'ebay' and db.get_bind().dialect.name == 'postgresql':
        # Both writers must observe the other's committed identity before inserting
        # another representation of the same line. The lock ends at commit/rollback.
        db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))'),
                   {'identity': f'ebay-sale:{getattr(db, "user_id", "local")}:{order_id}'})
    exact = query.filter_by(line_id=line_id).first()
    if exact is not None:
        return exact
    if marketplace != 'ebay' or not listing_id or not re.fullmatch(r'[0-9]{9,15}', listing_id):
        return None
    prefix = listing_id + '-'
    if re.fullmatch(r'[0-9]+', line_id):
        alias = prefix + line_id
    elif line_id.startswith(prefix) and re.fullmatch(r'[0-9]+', line_id[len(prefix):]):
        alias = line_id[len(prefix):]
    else:
        return None
    return query.filter_by(line_id=alias, listing_id=listing_id).first()


def record_sale(
    db: Session,
    *,
    marketplace: str,
    order_id: str,
    line_id: str,
    listing_id: str | None,
    quantity: int | None,
    sold_at: datetime | None,
    conversation_id: str | None = None,
) -> MarketplaceSale | None:
    """Store one sold line, once. Returns the new row, or None if already known.

    ``conversation_id`` is the item a check read the listing for. It decides the
    owner even when the marketplace link alone cannot: a sale always protects
    the listing it was found on.
    """
    exists = find_sale_line(db, marketplace, order_id, line_id, listing_id)
    if exists:
        return None
    conv = db.get(Conversation, conversation_id) if conversation_id else None
    if conv is None:
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
        db.flush()
        replace_stand_in(db, sale)
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

    eBay's account deletion deletes that account's data (``ebay_account_deletion``).
    Other events (listing changes) are acknowledged and left alone for now.
    """
    source, kind = event.get("source"), event.get("eventType")
    if (source, kind) not in SALE_EVENTS:
        if (source, kind) == ("ebay", "MARKETPLACE_ACCOUNT_DELETION"):
            from vendoo_studio.services import ebay_account_deletion

            # Vault or Keychain reads and database deletes: keep them off the event loop.
            await asyncio.to_thread(ebay_account_deletion.purge, _first(event.get("ids") or {}, "userId"), session_factory)
        return []
    from vendoo_studio.services.user_settings import sale_detection_enabled

    if not sale_detection_enabled(str(source)):
        return []
    if source == "ebay":
        from vendoo_studio.services.order_recovery import recover_ebay_notification

        order_id = _first(event.get("ids") or {}, "orderId")
        return await recover_ebay_notification(order_id, session_factory=session_factory) if order_id else []
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
