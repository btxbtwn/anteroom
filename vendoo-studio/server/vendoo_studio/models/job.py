from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow
from vendoo_studio.models.ownership import SellerOwned

ACTIVE_JOB_STATUSES = ("queued", "awaiting_extension", "dispatched")
RUNNING_JOB_STATUSES = ("dispatched",)
TERMINAL_JOB_STATUSES = ("completed", "cancelled", "failed", "imported", "dry_run")


def is_terminal_job_status(status: str | None) -> bool:
    return str(status or "") in TERMINAL_JOB_STATUSES


class Job(SellerOwned, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_jobs_seller_id"),
        ForeignKeyConstraint(["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
            name="fk_jobs_seller_conversation_id", deferrable=True, initially="DEFERRED"),
        # A seller's newest jobs, without sorting every job they have.
        Index("ix_jobs_seller_created_at", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    conversation_id: Mapped[str] = mapped_column(String, ForeignKey("conversations.id"), nullable=False, index=True)
    approved_revision_id: Mapped[str] = mapped_column(String, nullable=False)
    listing_snapshot: Mapped[Any] = mapped_column(JSON, nullable=False)
    status: Mapped[str | None] = mapped_column(String, default="queued", nullable=True)
    current_step: Mapped[str | None] = mapped_column(String, nullable=True)
    # Set only on jobs from the removed Send to Vendoo; kept as history.
    vendoo_item_id: Mapped[str | None] = mapped_column(String, nullable=True)
    vendoo_url: Mapped[str | None] = mapped_column(String, nullable=True)
    attempt_count: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Where several Machines share the database: which one runs this job, and
    # until when it counts as running unless renewed (services/api_job_lock.py).
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The seller pressed Cancel. The job stops at its next safe point, wherever it runs.
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=True)


class JobEvent(SellerOwned, Base):
    __tablename__ = "job_events"
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "job_id"], ["jobs.user_id", "jobs.id"],
            name="fk_job_events_seller_job_id", deferrable=True, initially="DEFERRED"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    job_id: Mapped[str] = mapped_column(String, ForeignKey("jobs.id"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    step: Mapped[str | None] = mapped_column(String, nullable=True)
    payload: Mapped[Any] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, nullable=True)


class VendooDraftCache(SellerOwned, Base):
    """The last Vendoo status Studio saw for a job — one row, status only. History: nothing writes it now.

    This used to be an appended ``job_events`` row carrying the whole Vendoo
    item. Sync writes one on every pull, an item is tens of kilobytes, and the
    table grew past 9 GB before anyone noticed. Nothing reads the rest of the
    item back from here, so nothing else is kept.
    """

    __tablename__ = "vendoo_draft_cache"
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "job_id"], ["jobs.user_id", "jobs.id"],
            name="fk_vendoo_draft_cache_seller_job_id", deferrable=True, initially="DEFERRED"),
    )

    job_id: Mapped[str] = mapped_column(String, ForeignKey("jobs.id"), primary_key=True, nullable=False)
    item_id: Mapped[str | None] = mapped_column(String, nullable=True)
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    step: Mapped[str | None] = mapped_column(String, nullable=True)
    payload: Mapped[Any] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=True)
