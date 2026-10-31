"""Seller-confirmed shares and offers on live marketplace listings, and offer floors.

Each row is one action on one listing. A row is reviewed (``preview``), then
sent at most once: ``sending`` is committed before Chrome is asked, so a lost
reply becomes ``unknown`` and is never repeated.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow
from vendoo_studio.models.ownership import SellerOwned


class ListingPromotion(SellerOwned, Base):
    __tablename__ = "listing_promotions"
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
            name="fk_listing_promotions_seller_conversation_id", deferrable=True, initially="DEFERRED"),
        ForeignKeyConstraint(["user_id", "automation_id"], ["promotion_automations.user_id", "promotion_automations.id"],
            name="fk_listing_promotions_seller_automation_id", deferrable=True, initially="DEFERRED"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    batch_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # Keep the journal when the local item is deleted.
    conversation_id: Mapped[str | None] = mapped_column(String, ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True)
    marketplace: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    listing_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    offer_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    floor_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discount_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The shipping discounts the marketplace offered at review; the seller picks one of them.
    shipping_options: Mapped[object | None] = mapped_column(JSON, nullable=True)
    shipping_discount_id: Mapped[str | None] = mapped_column(String, nullable=True)
    shipping_discount_label: Mapped[str | None] = mapped_column(String, nullable=True)
    like_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String, nullable=False, default="preview")
    note: Mapped[str | None] = mapped_column(String, nullable=True)
    marketplace_message: Mapped[str | None] = mapped_column(String, nullable=True)
    job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Set when a seller-enabled automation made this send; empty for one the seller started.
    automation_id: Mapped[str | None] = mapped_column(String, ForeignKey("promotion_automations.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MarketplaceOfferFloor(SellerOwned, Base):
    """The lowest price the seller accepts for offers on one marketplace listing."""

    __tablename__ = "marketplace_offer_floors"
    __table_args__ = (
        UniqueConstraint("user_id", "marketplace", "listing_id", name="uq_marketplace_offer_floor_listing"),
        ForeignKeyConstraint(["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
            name="fk_marketplace_offer_floors_seller_conversation_id", deferrable=True, initially="DEFERRED"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    marketplace: Mapped[str] = mapped_column(String, nullable=False)
    listing_id: Mapped[str] = mapped_column(String, nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(String, ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True)
    floor_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
