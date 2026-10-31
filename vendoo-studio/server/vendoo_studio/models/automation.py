"""Scheduled automations and the original asking price they drop from.

The task and each run live here. Per-item outcomes are written to the existing
``jobs`` table. Listing age comes from the first real listed date (a publish
job, a Vendoo date, then the marketplace row).

``item_price_baselines`` keeps the first asking price. A later drop is taken
from that original price. ``price_repeat`` on the task says whether each cycle
steps down again or the drop happens once.

Marketplace writes stay in ``automation_approvals`` until the seller approves
a specific item and action. A failed relist is recorded for manual recovery.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow
from vendoo_studio.models.ownership import SellerOwned


class Automation(SellerOwned, Base):
    __tablename__ = "automations"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_automations_seller_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # draft until the seller confirms; active runs; paused waits; completed is a one-off that finished.
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    timezone: Mapped[str] = mapped_column(String, nullable=False, default="UTC")
    schedule: Mapped[object] = mapped_column(JSON, nullable=False)
    filters: Mapped[object] = mapped_column(JSON, nullable=False)
    actions: Mapped[object] = mapped_column(JSON, nullable=False)
    marketplaces: Mapped[object] = mapped_column(JSON, nullable=False)
    # step walks the original price down each cycle. once drops a single time.
    # Empty means the price action's own repeat still applies.
    price_repeat: Mapped[str | None] = mapped_column(String, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class AutomationRun(SellerOwned, Base):
    """One firing of a task.

    ``scheduled_for`` is the slot that was due. A schedule slot is claimed once;
    a second insert for the same task and slot fails, which is what stops a
    restart from running it twice. Missed slots are not each given a row: the
    scheduler fires one catch-up run and moves ``next_run_at`` to the next
    future time.
    """

    __tablename__ = "automation_runs"
    __table_args__ = (
        UniqueConstraint("automation_id", "scheduled_for", name="uq_automation_runs_slot"),
        ForeignKeyConstraint(["user_id", "automation_id"], ["automations.user_id", "automations.id"],
            name="fk_automation_runs_seller_automation_id", deferrable=True, initially="DEFERRED"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    automation_id: Mapped[str] = mapped_column(String, ForeignKey("automations.id", ondelete="CASCADE"), nullable=False, index=True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # schedule or manual. A manual run uses its own slot so it does not consume the next timer fire.
    origin: Mapped[str] = mapped_column(String, nullable=False, default="schedule")
    # running, waiting, completed, failed
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[object | None] = mapped_column(JSON, nullable=True)


class AutomationApplication(SellerOwned, Base):
    """Last time an age rule acted on an item.

    The next pass waits until this is ``min_age_days`` old, so a daily check
    does not delist the same listing every morning. A new marketplace row
    (a new ``created_at``) still has to be old enough on its own.
    """

    __tablename__ = "automation_applications"
    __table_args__ = (
        UniqueConstraint("automation_id", "conversation_id", name="uq_automation_applications_item"),
        ForeignKeyConstraint(["user_id", "automation_id"], ["automations.user_id", "automations.id"],
            name="fk_automation_applications_seller_automation_id", deferrable=True, initially="DEFERRED"),
        ForeignKeyConstraint(["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
            name="fk_automation_applications_seller_conversation_id", deferrable=True, initially="DEFERRED"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    automation_id: Mapped[str] = mapped_column(String, ForeignKey("automations.id", ondelete="CASCADE"), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(String, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    listed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class AutomationRelistGap(SellerOwned, Base):
    """A marketplace that stayed down after a relist retried it.

    ``state`` is the seller-facing words. An open row (``resolved_at`` empty)
    is what ``anteroom errors list`` and the Automations page show.
    """

    __tablename__ = "automation_relist_gaps"
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "automation_id"], ["automations.user_id", "automations.id"],
            name="fk_automation_relist_gaps_seller_automation_id", deferrable=True, initially="DEFERRED"),
        ForeignKeyConstraint(["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
            name="fk_automation_relist_gaps_seller_conversation_id", deferrable=True, initially="DEFERRED"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    automation_id: Mapped[str] = mapped_column(String, ForeignKey("automations.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    conversation_id: Mapped[str] = mapped_column(String, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    marketplace: Mapped[str] = mapped_column(String, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String, nullable=False, default="down, needs relist")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AutomationApproval(SellerOwned, Base):
    """A regenerated draft waiting for the seller to publish it."""

    __tablename__ = "automation_approvals"
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "automation_id"], ["automations.user_id", "automations.id"],
            name="fk_automation_approvals_seller_automation_id", deferrable=True, initially="DEFERRED"),
        ForeignKeyConstraint(["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
            name="fk_automation_approvals_seller_conversation_id", deferrable=True, initially="DEFERRED"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    automation_id: Mapped[str] = mapped_column(String, ForeignKey("automations.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    conversation_id: Mapped[str] = mapped_column(String, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    revision_id: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[str] = mapped_column(String, nullable=False, server_default="publish")
    targets: Mapped[object] = mapped_column(JSON, nullable=False, server_default="{}")
    review_basis: Mapped[str] = mapped_column(String, nullable=False, server_default="")
    marketplaces: Mapped[object] = mapped_column(JSON, nullable=False)
    # pending, approved, dismissed
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ItemPriceBaseline(SellerOwned, Base):
    """The asking price a drop is measured from. Set once, never replaced."""

    __tablename__ = "item_price_baselines"
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
            name="fk_item_price_baselines_seller_conversation_id", deferrable=True, initially="DEFERRED"),
    )

    conversation_id: Mapped[str] = mapped_column(String, ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True, nullable=False)
    original_price: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
