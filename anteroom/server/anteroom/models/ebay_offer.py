"""Seller-approved offer rules and a durable journal of buyer-facing sends."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from anteroom.database import Base
from anteroom.models.conversation import new_id, utcnow
from anteroom.models.ownership import SellerOwned


class EbayOfferPolicy(SellerOwned, Base):
    """Explicit approval for current and future listings on one seller's eBay account."""
    __tablename__ = "ebay_offer_policies"
    __table_args__ = (UniqueConstraint("user_id", "account_id", name="uq_ebay_offer_policy_account"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    account_id: Mapped[str] = mapped_column(String, nullable=False)
    approval_id: Mapped[str] = mapped_column(String, nullable=False, default=new_id)
    discount_percent: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    waterfall_steps: Mapped[Any] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class EbayOfferRule(SellerOwned, Base):
    __tablename__ = "ebay_offer_rules"
    __table_args__ = (
        UniqueConstraint("user_id", "account_id", "listing_id", name="uq_ebay_offer_rule_listing"),
        UniqueConstraint("user_id", "id", name="uq_ebay_offer_rules_seller_id"),
        ForeignKeyConstraint(["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
            name="fk_ebay_offer_rules_seller_conversation_id", deferrable=True, initially="DEFERRED"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    account_id: Mapped[str] = mapped_column(String, nullable=False)
    listing_id: Mapped[str] = mapped_column(String, nullable=False)
    # Keep the listing/account journal when a local item is deleted and reimported.
    conversation_id: Mapped[str | None] = mapped_column(String, ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True)
    discount_percent: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    waterfall_steps: Mapped[Any] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class EbayOfferAttempt(SellerOwned, Base):
    __tablename__ = "ebay_offer_attempts"
    __table_args__ = (
        Index("ix_ebay_offer_attempts_seller_sent", "user_id", "sent_at"),
        ForeignKeyConstraint(["user_id", "rule_id"], ["ebay_offer_rules.user_id", "ebay_offer_rules.id"],
            name="fk_ebay_offer_attempts_seller_rule_id", deferrable=True, initially="DEFERRED"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    rule_id: Mapped[str] = mapped_column(String, ForeignKey("ebay_offer_rules.id", ondelete="CASCADE"), nullable=False, index=True)
    current_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    offer_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    discount_percent: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    waterfall_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String, nullable=False, default="preview")
    note: Mapped[str | None] = mapped_column(String, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    recipient_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
