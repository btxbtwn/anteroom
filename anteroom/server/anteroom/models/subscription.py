"""Hosted subscription plans and monthly Smart Credit / listed-item usage ledger."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, PrimaryKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from anteroom.database import Base
from anteroom.models.conversation import new_id, utcnow
from anteroom.models.ownership import SellerOwned


class SellerPlan(SellerOwned, Base):
    """The subscription tier assigned to a hosted seller account.

    Accounts without a row default to the Free tier unless their email matches
    the configured unlimited-account list. Sellers can read their own row under
    Postgres RLS, while plan changes go through operator/billing functions.
    """

    __tablename__ = "seller_plans"
    __table_args__ = (PrimaryKeyConstraint("user_id", name="pk_seller_plans"),)

    plan_id: Mapped[str] = mapped_column(String(32), nullable=False, default="free", server_default="free")
    billing_interval: Mapped[str] = mapped_column(
        String(16), nullable=False, default="monthly", server_default="monthly",
    )
    assigned_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class PlanUsageEvent(SellerOwned, Base):
    """Append-only monthly ledger of Smart Credit charges, refunds, and first-time listed items.

    Rows are bucketed by UTC calendar month (``period`` = ``YYYY-MM``). Credit
    events carry a positive integer ``credits`` (or negative for an automatic
    refund when a photo call fails). ``listed_item`` events carry ``credits = 0``
    and record the first time a ``conversation_id`` is listed in a month so
    cross-listing the same item across marketplaces counts once.
    """

    __tablename__ = "plan_usage_events"
    __table_args__ = (
        Index("ix_plan_usage_events_owner_period", "user_id", "period", "kind"),
        Index("ix_plan_usage_events_owner_period_conv", "user_id", "period", "kind", "conversation_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    credits: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    conversation_id: Mapped[str | None] = mapped_column(String, nullable=True)
    reference_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
