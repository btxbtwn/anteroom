"""A new listing the seller opened and left without adding anything.

New listing makes its draft at once, so the editor has somewhere to save photos
and fields. When the seller leaves it before adding a photo or typing, the app
asks to discard it, the way Apple Notes drops a note left empty. The check runs
here, in the same transaction as the delete, so a draft that gained anything
in the meantime is kept.
"""
from __future__ import annotations

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from vendoo_studio.models.conversation import Conversation, Message, Photo
from vendoo_studio.models.job import Job
from vendoo_studio.models.listing import Listing, ListingRevision
from vendoo_studio.models.listing_video import ListingVideo
from vendoo_studio.models.marketplace_listing import MarketplaceListing

# The title the New listing button gives a draft.
NEW_LISTING_TITLE = "New Listing"

_CONTENT = (Photo, Message, Listing, ListingRevision, Job, ListingVideo, MarketplaceListing)


def is_blank(db: Session, conv: Conversation) -> bool:
    """True while the draft holds nothing the seller added: no photo, text, chat or marketplace record."""
    if (conv.title or "").strip() not in ("", NEW_LISTING_TITLE):
        return False
    if (conv.notes or "").strip() or conv.box_id or conv.settled_at:
        return False
    return not any(
        db.scalar(select(exists().where(model.conversation_id == conv.id))) for model in _CONTENT
    )
