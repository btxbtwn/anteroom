from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from anteroom.models.conversation import Conversation

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSONB

from anteroom.database import Base
from anteroom.models.conversation import new_id, utcnow
from anteroom.models.ownership import SellerOwned
from anteroom.models.inventory_projection import InventoryProjection


class Listing(SellerOwned, Base):
    __tablename__ = "listings"
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
            name="fk_listings_seller_conversation_id", deferrable=True, initially="DEFERRED"),
    )

    conversation_id: Mapped[str] = mapped_column(String, ForeignKey("conversations.id"), primary_key=True, nullable=False)
    current_revision_id: Mapped[str | None] = mapped_column(String, nullable=True)
    validation_status: Mapped[str | None] = mapped_column(String, nullable=True)
    validation_errors: Mapped[Any] = mapped_column(JSON, nullable=True)
    # Seller opt-in draft scheduling (off by default; unscheduled automatically on edit).
    ready_to_list: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    scheduled_publish_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    scheduled_marketplaces: Mapped[Any] = mapped_column(JSON, nullable=True)
    scheduled_revision_id: Mapped[str | None] = mapped_column(String, nullable=True)
    scheduled_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scheduled_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    scheduled_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_content_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=True)

    conversation: Mapped[Conversation] = relationship("Conversation", back_populates="listing", primaryjoin="and_(Conversation.id == foreign(Listing.conversation_id), Conversation.user_id == Listing.user_id)")


def revision_quantity(context):
    value = (context.get_current_parameters().get("listing_json") or {}).get("quantity")
    if value is None:
        return None
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
    # Inventory reads extract several fields from large marketplace forms.
    # PostgreSQL JSON reparses the whole form for each field; JSONB does not.
    listing_json: Mapped[Any] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    # Routine inventory reads never decompress the large marketplace forms.
    # Database generation keeps ORM, import and direct SQL writes consistent.
    inventory_fields: Mapped[Any] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
                                                Computed(InventoryProjection(), persisted=True), nullable=False)
    # Status reads scan quantity history, never the large marketplace form JSON.
    inventory_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True, default=revision_quantity)
    # SHA-256 of the normalized listing JSON, so inventory reads compare revisions
    # without loading or decompressing full marketplace forms.
    normalized_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    generation_feedback: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    parent_revision_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, nullable=True)
