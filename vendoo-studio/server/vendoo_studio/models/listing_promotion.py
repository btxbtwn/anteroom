"""Seller-confirmed shares and offers on live marketplace listings, and offer floors.

Each row is one action on one listing. A row is reviewed (``preview``), then
sent at most once: ``sending`` is committed before Chrome is asked, so a lost
reply becomes ``unknown`` and is never repeated.
"""
from sqlalchemy import ForeignKeyConstraint, JSON, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from vendoo_studio.database import Base
from vendoo_studio.models.ownership import SellerOwned
from vendoo_studio.models.conversation import new_id, utcnow


class ListingPromotion(SellerOwned, Base):
    __tablename__ = "listing_promotions"
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
            name="fk_listing_promotions_seller_conversation_id", deferrable=True, initially="DEFERRED"),
        ForeignKeyConstraint(["user_id", "automation_id"], ["promotion_automations.user_id", "promotion_automations.id"],
            name="fk_listing_promotions_seller_automation_id", deferrable=True, initially="DEFERRED"),
    )

    id = Column(String, primary_key=True, default=new_id)
    batch_id = Column(String, nullable=False, index=True)
    # Keep the journal when the local item is deleted.
    conversation_id = Column(String, ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True)
    marketplace = Column(String, nullable=False)
    action = Column(String, nullable=False)
    listing_id = Column(String, nullable=False, index=True)
    title = Column(String, nullable=True)
    price_cents = Column(Integer, nullable=True)
    offer_price_cents = Column(Integer, nullable=True)
    floor_cents = Column(Integer, nullable=True)
    discount_percent = Column(Integer, nullable=True)
    # The shipping discounts the marketplace offered at review; the seller picks one of them.
    shipping_options = Column(JSON, nullable=True)
    shipping_discount_id = Column(String, nullable=True)
    shipping_discount_label = Column(String, nullable=True)
    like_count = Column(Integer, nullable=True)
    state = Column(String, nullable=False, default="preview")
    note = Column(String, nullable=True)
    marketplace_message = Column(String, nullable=True)
    job_id = Column(String, nullable=True)
    # Set when a seller-enabled automation made this send; empty for one the seller started.
    automation_id = Column(String, ForeignKey("promotion_automations.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    expires_at = Column(DateTime, nullable=False)
    sent_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class MarketplaceOfferFloor(SellerOwned, Base):
    """The lowest price the seller accepts for offers on one marketplace listing."""

    __tablename__ = "marketplace_offer_floors"
    __table_args__ = (
        UniqueConstraint("user_id", "marketplace", "listing_id", name="uq_marketplace_offer_floor_listing"),
        ForeignKeyConstraint(["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
            name="fk_marketplace_offer_floors_seller_conversation_id", deferrable=True, initially="DEFERRED"),
    )

    id = Column(String, primary_key=True, default=new_id)
    marketplace = Column(String, nullable=False)
    listing_id = Column(String, nullable=False)
    conversation_id = Column(String, ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True)
    floor_cents = Column(Integer, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
