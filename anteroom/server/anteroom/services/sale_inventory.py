"""Interpret sale alerts without assuming one unit sold exhausts a listing."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from anteroom.models.conversation import Conversation
from anteroom.models.listing import Listing, ListingRevision
from anteroom.models.marketplace_listing import MarketplaceListing
from anteroom.models.marketplace_sale import MarketplaceSale, PAID_STATUSES
from anteroom.repositories.marketplace_listings import as_dict
from anteroom.services.vendoo_import import parse_notes


def _time(value):
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)
    except (ValueError, TypeError):
        return None


StockKey = tuple[str, str, str | None]


def stock_after_sale(db: Session, conv_id: str, marketplace: str, listing_id: str | None) -> dict:
    """Use stock read after the latest sale notice; never subtract sales from edited/imported quantities."""
    key = (conv_id, marketplace, listing_id)
    return stocks_after_sales(db, [key])[key]


def stocks_after_sales(db: Session, keys: Iterable[StockKey]) -> dict[StockKey, dict]:
    """``stock_after_sale`` for many ``(conversation, marketplace, listing)`` keys, in at most five queries.

    Lists of sales (the Sales Tracker, Today's still-listed alerts) read every
    sale's stock. One query per sale held the seller's connection for as long
    as the list was long.
    """
    keys = list(dict.fromkeys(keys))
    result: dict[StockKey, dict] = {key: _unknown() for key in keys}
    if not keys:
        return result
    lines: dict[tuple[str | None, str, str | None], list[MarketplaceSale]] = {}
    for line in db.query(MarketplaceSale).filter(MarketplaceSale.conversation_id.in_({key[0] for key in keys})):
        lines.setdefault((line.conversation_id, line.marketplace, line.listing_id), []).append(line)
    sold = [key for key in keys if any(line.order_status in PAID_STATUSES for line in lines.get(key, ()))]
    if not sold:
        return result
    ids = {key[0] for key in sold}
    records = {(row.conversation_id, row.marketplace): as_dict(row) for row in db.query(MarketplaceListing)
               .filter(MarketplaceListing.conversation_id.in_(ids)).populate_existing()}
    notes = {conv.id: parse_notes(conv.notes) for conv in db.query(Conversation).filter(Conversation.id.in_(ids))}
    undecided = []
    for key in sold:
        stock = _observed_stock(key, lines[key], records.get(key[:2], {}), notes.get(key[0], {}))
        if stock is None:
            undecided.append(key)
        else:
            result[key] = stock
    if not undecided:
        return result
    current = {listing.conversation_id: listing.current_revision_id for listing in
               db.query(Listing).filter(Listing.conversation_id.in_({key[0] for key in undecided}))}
    wanted = {*(current.get(key[0]) for key in undecided),
              *(records.get(key[:2], {}).get("revision_id") for key in undecided)} - {None}
    revisions: dict[str | None, ListingRevision] = {revision.id: revision for revision in
                 db.query(ListingRevision).filter(ListingRevision.id.in_(wanted))} if wanted else {}
    for key in undecided:
        record = records.get(key[:2], {})
        result[key] = _recorded_stock(key, lines[key], record, revisions.get(current.get(key[0])),
                                      revisions.get(record.get("revision_id")))
    return result


def _unknown() -> dict:
    return {"status": "unknown", "remaining": None}


def _observed_stock(key: StockKey, lines: list[MarketplaceSale], record: dict, notes: dict) -> dict | None:
    """The stock a marketplace read after the latest sale notice shows, or None when no read decides it."""
    _conv_id, marketplace, listing_id = key
    changed_at = max(filter(None, (_time(value) for line in lines for value in (line.received_at, line.order_observed_at))), default=None)
    from anteroom.services.marketplace_delist import current_listing
    from anteroom.services.marketplace_identity import listing_id as reference_id

    marks, delists = notes.get("marketplaceMarks"), notes.get("marketplaceDelists")
    mark = marks.get(marketplace) if isinstance(marks, dict) else None
    deleted = delists.get(marketplace) if isinstance(delists, dict) else None
    url, _source = current_listing(marketplace, record.get("listing_url"), None, mark if isinstance(mark, dict) else {},
                                   deleted if isinstance(deleted, dict) else {})
    current_id = reference_id(marketplace, url)
    if current_id and listing_id and current_id != listing_id:
        return _unknown()
    observed = record.get("inventory_observation") or {}
    checked, received = _time(observed.get("checked_at")), changed_at
    if observed.get("listing_id") == listing_id and checked and received and checked >= received:
        quantity = observed.get("quantity")
        if observed.get("state") == "active" and type(quantity) is int and quantity >= 0:
            return {"status": "remaining" if quantity else "sold_out", "remaining": quantity}
        if observed.get("state") == "sold_out":
            return {"status": "sold_out", "remaining": 0}
        # An ended or inactive listing does not establish stock exhaustion.
        return _unknown()
    return None


def _recorded_stock(key: StockKey, lines: list[MarketplaceSale], record: dict,
                    revision: ListingRevision | None, published: ListingRevision | None) -> dict:
    """Without a fresh read, a single-unit item is sold out; anything listed with more units is unknown."""
    from anteroom.services.ebay_variations import total_quantity

    marketplace = key[1]
    paid = [line for line in lines if line.order_status in PAID_STATUSES]
    observed = record.get("inventory_observation") or {}
    # A listing with variations holds stock per variation: its total counts, not the single quantity field.
    quantities = [revision.listing_json.get("quantity") if revision else None,
                  published.listing_json.get("quantity") if published else None,
                  *[total_quantity(source.listing_json) for source in (revision, published)
                    if source and marketplace == "ebay" and isinstance(source.listing_json, dict)],
                  observed.get("quantity"), *[line.quantity for line in paid]]
    if any(type(quantity) is int and quantity > 1 for quantity in quantities):
        return _unknown()
    # Keep the established single-item sale behavior until a direct read says
    # otherwise. A multi-unit item always needs a fresh marketplace stock read.
    return {"status": "sold_out", "remaining": None}
