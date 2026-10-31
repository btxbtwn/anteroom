from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vendoo_studio.models.conversation import Conversation

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow
from vendoo_studio.models.ownership import SellerOwned


class Listing(SellerOwned, Base):
    __tablename__ = "listings"
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
            name="fk_listings_seller_conversation_id", deferrable=True, initially="DEFERRED"),
    )

    conversation_id: Mapped[str] = mapped_column(String, ForeignKey("conversations.id"), primary_key=True, nullable=False)
    current_revision_id: Mapped[str | None] = mapped_column(String, nullable=True)
    validation_status: Mapped[str | None] = mapped_column(String, nullable=True)
    validation_errors: Mapped[object | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=True)

    conversation: Mapped[Conversation] = relationship("Conversation", back_populates="listing", primaryjoin="and_(Conversation.id == foreign(Listing.conversation_id), Conversation.user_id == Listing.user_id)")


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

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    conversation_id: Mapped[str] = mapped_column(String, ForeignKey("conversations.id"), nullable=False, index=True)
    listing_json: Mapped[object] = mapped_column(JSON, nullable=False)
    # Status reads scan quantity history, never the large marketplace form JSON.
    inventory_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True, default=revision_quantity)
    source: Mapped[str] = mapped_column(String, nullable=False)
    parent_revision_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, nullable=True)
