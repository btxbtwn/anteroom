"""Save a new direct eBay import in one local transaction."""
from sqlalchemy import or_
from sqlalchemy.orm import Session

from vendoo_studio.models.conversation import Conversation, Photo, new_id
from vendoo_studio.models.listing import Listing, ListingRevision
from vendoo_studio.models.marketplace_listing import MarketplaceListing


def owners_by_id(db: Session, listing_id: str) -> list[Conversation]:
    """Also recognise IDs journaled before a direct action confirmed its URL."""
    return db.query(Conversation).join(MarketplaceListing, MarketplaceListing.conversation_id == Conversation.id).filter(
        MarketplaceListing.marketplace == "ebay",
        or_(MarketplaceListing.listing_id == listing_id, MarketplaceListing.ebay_listing_id == listing_id),
    ).all()


def create_import(db: Session, snapshot: dict, listing_id: str, photos: list[dict]) -> Conversation:
    conv = Conversation(id=new_id(), title=snapshot["title"], status="active")
    revision = ListingRevision(id=new_id(), conversation_id=conv.id, listing_json=snapshot, source="ebay_import")
    try:
        db.add(conv)
        db.flush()
        db.add_all([
            revision,
            Listing(conversation_id=conv.id, current_revision_id=revision.id),
            MarketplaceListing(conversation_id=conv.id, marketplace="ebay", state="published",
                               listing_id=listing_id, ebay_listing_id=listing_id, revision_id=revision.id,
                               listing_url=f"https://www.ebay.com/itm/{listing_id}"),
            *(Photo(conversation_id=conv.id, display_order=index, **meta) for index, meta in enumerate(photos)),
        ])
        db.commit()
        return conv
    except BaseException:
        db.rollback()
        raise
