"""Seller-enabled scheduled shares and automatic offers to likers, and their rounds.

An automation is saved off and does nothing until the seller turns it on. Any
edit turns it off again. Each send it makes is a ``listing_promotions`` row
carrying its ``automation_id``; each round is a ``promotion_runs`` row.
"""

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
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow
from vendoo_studio.models.ownership import SellerOwned


class PromotionAutomation(SellerOwned, Base):
    __tablename__ = "promotion_automations"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_promotion_automations_seller_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    marketplace: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # "all" live listings on the marketplace, or the chosen ``conversation_ids``; never the excluded ones.
    scope: Mapped[str] = mapped_column(String, nullable=False, default="all")
    conversation_ids: Mapped[Any] = mapped_column(JSON, nullable=False, default=list)
    excluded_ids: Mapped[Any] = mapped_column(JSON, nullable=False, default=list)
    min_days_listed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Shares only.
    share_order: Mapped[str | None] = mapped_column(String, nullable=True)
    speed: Mapped[str | None] = mapped_column(String, nullable=True)
    daily_cap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    times: Mapped[Any] = mapped_column(JSON, nullable=True)
    timezone: Mapped[str | None] = mapped_column(String, nullable=True)
    # Offers only. No shipping label means no shipping discount.
    discount_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minimum_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shipping_label: Mapped[str | None] = mapped_column(String, nullable=True)
    paused_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    like_events_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class PromotionRun(SellerOwned, Base):
    __tablename__ = "promotion_runs"
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "automation_id"], ["promotion_automations.user_id", "promotion_automations.id"],
            name="fk_promotion_runs_seller_automation_id", deferrable=True, initially="DEFERRED"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    automation_id: Mapped[str] = mapped_column(String, ForeignKey("promotion_automations.id", ondelete="CASCADE"), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(String, nullable=True)
