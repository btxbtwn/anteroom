"""Seller-approved offer rules and a durable journal of buyer-facing sends."""
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from vendoo_studio.database import Base
from vendoo_studio.models.ownership import SellerOwned
from vendoo_studio.models.conversation import new_id, utcnow


class EbayOfferRule(SellerOwned, Base):
    __tablename__ = "ebay_offer_rules"
    __table_args__ = (UniqueConstraint("account_id", "listing_id", name="uq_ebay_offer_rule_listing"),)

    id = Column(String, primary_key=True, default=new_id)
    account_id = Column(String, nullable=False)
    listing_id = Column(String, nullable=False)
    # Keep the listing/account journal when a local item is deleted and reimported.
    conversation_id = Column(String, ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True)
    discount_percent = Column(Integer, nullable=False)
    minimum_price_cents = Column(Integer, nullable=False)
    enabled = Column(Boolean, nullable=False, default=False)
    last_attempt_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class EbayOfferAttempt(SellerOwned, Base):
    __tablename__ = "ebay_offer_attempts"

    id = Column(String, primary_key=True, default=new_id)
    rule_id = Column(String, ForeignKey("ebay_offer_rules.id", ondelete="CASCADE"), nullable=False, index=True)
    current_price_cents = Column(Integer, nullable=False)
    offer_price_cents = Column(Integer, nullable=False)
    discount_percent = Column(Integer, nullable=False)
    minimum_price_cents = Column(Integer, nullable=False)
    state = Column(String, nullable=False, default="preview")
    note = Column(String, nullable=True)
    recipient_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    expires_at = Column(DateTime, nullable=False)
