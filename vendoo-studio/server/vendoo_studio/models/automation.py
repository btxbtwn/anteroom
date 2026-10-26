"""Scheduled automations and the original asking price they drop from.

The task and each run live here. Per-item outcomes are written to the existing
``jobs`` table. Listing age is read from ``marketplace_listings.created_at``
(when that row was first inserted), not from this table.

``item_price_baselines`` keeps the first asking price. A relist that inserts a
new marketplace row starts the age clock over, and a later 10% drop is still
taken from this original price rather than from the reduced one.
"""
from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow


class Automation(Base):
    __tablename__ = "automations"

    id = Column(String, primary_key=True, default=new_id)
    name = Column(String, nullable=False)
    prompt = Column(Text, nullable=True)
    # draft until the seller confirms; active runs; paused waits; completed is a one-off that finished.
    status = Column(String, nullable=False, default="draft")
    timezone = Column(String, nullable=False, default="UTC")
    schedule = Column(JSON, nullable=False)
    filters = Column(JSON, nullable=False)
    actions = Column(JSON, nullable=False)
    marketplaces = Column(JSON, nullable=False)
    confirmed_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True, index=True)
    last_run_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow)


class AutomationRun(Base):
    """One firing of a task.

    ``scheduled_for`` is the slot that was due. A schedule slot is claimed once;
    a second insert for the same task and slot fails, which is what stops a
    restart from running it twice. Missed slots are not each given a row: the
    scheduler fires one catch-up run and moves ``next_run_at`` to the next
    future time.
    """

    __tablename__ = "automation_runs"
    __table_args__ = (UniqueConstraint("automation_id", "scheduled_for", name="uq_automation_runs_slot"),)

    id = Column(String, primary_key=True, default=new_id)
    automation_id = Column(String, ForeignKey("automations.id", ondelete="CASCADE"), nullable=False, index=True)
    scheduled_for = Column(DateTime, nullable=False)
    # schedule or manual. A manual run uses its own slot so it does not consume the next timer fire.
    origin = Column(String, nullable=False, default="schedule")
    # running, waiting, completed, failed
    status = Column(String, nullable=False, default="running")
    dry_run = Column(Boolean, nullable=False, default=False)
    attempt_count = Column(Integer, nullable=False, default=0)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    next_attempt_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)
    summary = Column(JSON, nullable=True)


class AutomationApplication(Base):
    """Last time an age rule acted on an item.

    The next pass waits until this is ``min_age_days`` old, so a daily check
    does not delist the same listing every morning. A new marketplace row
    (a new ``created_at``) still has to be old enough on its own.
    """

    __tablename__ = "automation_applications"
    __table_args__ = (UniqueConstraint("automation_id", "conversation_id", name="uq_automation_applications_item"),)

    id = Column(String, primary_key=True, default=new_id)
    automation_id = Column(String, ForeignKey("automations.id", ondelete="CASCADE"), nullable=False, index=True)
    conversation_id = Column(String, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    listed_at = Column(DateTime, nullable=True)
    applied_at = Column(DateTime, nullable=False, default=utcnow)


class ItemPriceBaseline(Base):
    """The asking price a drop is measured from. Set once, never replaced."""

    __tablename__ = "item_price_baselines"

    conversation_id = Column(String, ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True)
    original_price = Column(Float, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)
