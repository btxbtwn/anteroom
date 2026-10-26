"""Save directly imported marketplace inventory in one local transaction."""
from sqlalchemy import or_
from sqlalchemy.orm import Session

from vendoo_studio.models.conversation import Conversation, Photo, new_id
from vendoo_studio.models.listing import Listing, ListingRevision
from vendoo_studio.models.marketplace_listing import MarketplaceListing


def owners_by_id(db: Session, marketplace: str, listing_id: str) -> list[Conversation]:
    """Also recognise IDs journaled before a direct action confirmed its URL."""
    identifiers = [MarketplaceListing.listing_id == listing_id]
    if marketplace == "ebay":
        identifiers.append(MarketplaceListing.ebay_listing_id == listing_id)
    elif marketplace == "etsy":
        identifiers.append(MarketplaceListing.draft_url.in_([
            f"https://www.etsy.com/your/shops/me/listing-editor/edit/{listing_id}",
            f"https://www.etsy.com/your/shops/me/listing-editor/edit/{listing_id}/",
        ]))
    return db.query(Conversation).join(MarketplaceListing, MarketplaceListing.conversation_id == Conversation.id).filter(
        MarketplaceListing.marketplace == marketplace, or_(*identifiers),
    ).all()


def create_import(db: Session, marketplace: str, snapshot: dict, listing_id: str, photos: list[dict], binding: dict | None = None) -> Conversation:
    public_url = {"ebay": f"https://www.ebay.com/itm/{listing_id}", "etsy": f"https://www.etsy.com/listing/{listing_id}"}[marketplace]
    conv = Conversation(id=new_id(), title=snapshot["title"], status="active")
    revision = ListingRevision(id=new_id(), conversation_id=conv.id, listing_json=snapshot, source=f"{marketplace}_import")
    try:
        db.add(conv)
        db.flush()
        db.add_all([
            revision,
            Listing(conversation_id=conv.id, current_revision_id=revision.id),
            MarketplaceListing(conversation_id=conv.id, marketplace=marketplace, state="published",
                               listing_id=listing_id, revision_id=revision.id, listing_url=public_url,
                               **({"ebay_listing_id": listing_id} if marketplace == "ebay" else {}), **(binding or {})),
            *(Photo(conversation_id=conv.id, display_order=index, **meta) for index, meta in enumerate(photos)),
        ])
        db.commit()
        return conv
    except BaseException:
        db.rollback()
        raise
