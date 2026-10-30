from __future__ import annotations


from sqlalchemy import ForeignKeyConstraint, Column, String, DateTime, ForeignKey, Index, Integer, JSON
from sqlalchemy.orm import relationship

from vendoo_studio.database import Base
from vendoo_studio.models.ownership import SellerOwned
from vendoo_studio.models.conversation import new_id, utcnow


class Listing(SellerOwned, Base):
    __tablename__ = "listings"
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
            name="fk_listings_seller_conversation_id", deferrable=True, initially="DEFERRED"),
    )

    conversation_id = Column(String, ForeignKey("conversations.id"), primary_key=True)
    current_revision_id = Column(String, nullable=True)
    validation_status = Column(String, nullable=True)
    validation_errors = Column(JSON, nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    conversation = relationship("Conversation", back_populates="listing", primaryjoin="and_(Conversation.id == foreign(Listing.conversation_id), Conversation.user_id == Listing.user_id)")


def revision_quantity(context):
    value = (context.get_current_parameters().get("listing_json") or {}).get("quantity")
    try:
        quantity = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return quantity if 0 < quantity <= 2_147_483_647 else None


class ListingRevision(SellerOwned, Base):
    __tablename__ = "listing_revisions"
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
            name="fk_listing_revisions_seller_conversation_id", deferrable=True, initially="DEFERRED"),
        Index("ix_listing_revisions_quantity_history", "user_id", "conversation_id", "created_at", "inventory_quantity"),
    )

    id = Column(String, primary_key=True, default=new_id)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False, index=True)
    listing_json = Column(JSON, nullable=False)
    # Status reads scan quantity history, never the large marketplace form JSON.
    inventory_quantity = Column(Integer, nullable=True, default=revision_quantity)
    source = Column(String, nullable=False)
    parent_revision_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)
