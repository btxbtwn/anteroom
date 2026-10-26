"""Interpret sale alerts without assuming one unit sold exhausts a listing."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from vendoo_studio.models.listing import ListingRevision
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services.vendoo_import import parse_notes


def _time(value):
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)
    except (ValueError, TypeError):
        return None


def stock_after_sale(db: Session, conv_id: str, marketplace: str, listing_id: str | None) -> dict:
    """Use stock read after the latest sale notice; never subtract sales from edited/imported quantities."""
    unknown = {"status": "unknown", "remaining": None}
    latest = db.query(MarketplaceSale).filter_by(conversation_id=conv_id, marketplace=marketplace,
                                               listing_id=listing_id).order_by(MarketplaceSale.received_at.desc()).first()
    if latest is None:
        return unknown
    record = MarketplaceListingRepo(db).get(conv_id, marketplace)
    from vendoo_studio.services.marketplace_identity import listing_id as reference_id

    conv = ConversationRepo(db).get(conv_id)
    notes = parse_notes(conv.notes) if conv else {}
    marks = notes.get("marketplaceMarks") or {}
    mark = marks.get(marketplace) if isinstance(marks, dict) else None
    marked_id = reference_id(marketplace, mark.get("url")) if isinstance(mark, dict) and mark.get("state") == "listed" else None
    current_id = marked_id or reference_id(marketplace, record.get("listing_url"))
    if current_id and listing_id and current_id != listing_id:
        return unknown
    observed = record.get("inventory_observation") or {}
    checked, received = _time(observed.get("checked_at")), _time(latest.received_at)
    if observed.get("listing_id") == listing_id and checked and received and checked >= received:
        quantity = observed.get("quantity")
        if observed.get("state") == "active" and type(quantity) is int and quantity >= 0:
            return {"status": "remaining" if quantity else "sold_out", "remaining": quantity}
        if observed.get("state") == "sold_out":
            return {"status": "sold_out", "remaining": 0}
        # An ended or inactive listing does not establish stock exhaustion.
        return unknown
    listing = ListingRepo(db).get_current(conv_id)
    revision = db.get(ListingRevision, listing.current_revision_id) if listing and listing.current_revision_id else None
    quantity = revision.listing_json.get("quantity") if revision else None
    if type(quantity) is int and quantity > 1:
        return unknown
    # Keep the established single-item sale behavior until a direct read says
    # otherwise. A multi-unit item always needs a fresh marketplace stock read.
    return {"status": "sold_out", "remaining": None}
