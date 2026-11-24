"""Seller goals and Needs Attention snoozes and resolutions for the Home dashboard."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from anteroom.database import Base
from anteroom.models.conversation import new_id, utcnow
from anteroom.models.ownership import SellerOwned

GOAL_PERIODS = ("weekly", "monthly", "quarterly", "yearly")
GOAL_METRICS = ("profit", "sales", "listings", "sell_through_rate")


class SellerGoal(SellerOwned, Base):
    __tablename__ = "seller_goals"
    __table_args__ = (
        UniqueConstraint("user_id", "period", "metric", name="uq_seller_goals_owner_period_metric"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    metric: Mapped[str] = mapped_column(String(32), nullable=False)
    target: Mapped[float] = mapped_column(Float, nullable=False)
    label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class AttentionDismissal(SellerOwned, Base):
    __tablename__ = "attention_dismissals"
    __table_args__ = (
        UniqueConstraint("user_id", "item_key", name="uq_attention_dismissals_owner_key"),
        Index("ix_attention_dismissals_owner_expires", "user_id", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    item_key: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    # When the seller snoozed or resolved it.
    dismissed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    # Snooze: hidden until this time. Null on a resolved item.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Resolve: hidden while the item's fingerprint stays this value. Null on a snooze.
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
