from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
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

ACTIVE_JOB_STATUSES = ("queued", "awaiting_extension", "dispatched")
DISPATCHABLE_JOB_STATUSES = ("queued", "awaiting_extension")
RUNNING_JOB_STATUSES = ("dispatched",)
TERMINAL_JOB_STATUSES = ("completed", "cancelled", "failed", "imported", "dry_run")


# A send that was interrupted rather than refused. It resumes once on its own;
# the resumed run picks up its checkpointed draft instead of making a second.
ABANDONED_SEND = "Send to Vendoo stopped before it finished. Click Send to Vendoo again."
RECONNECTED_SEND = "Chrome reconnected during Send to Vendoo. Click Send to Vendoo again."
INTERRUPTED_SEND_ERRORS = frozenset({ABANDONED_SEND, RECONNECTED_SEND})
AUTO_RETRY_EVENT = "vendoo_api_auto_retry"


def is_terminal_job_status(status: str | None) -> bool:
    return str(status or "") in TERMINAL_JOB_STATUSES


def is_vendoo_api_step(step: str | None) -> bool:
    """True for API create/save work — never hand these to the form-filler."""
    return str(step or "").startswith("vendoo_api")


class Job(SellerOwned, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_jobs_seller_id"),
        ForeignKeyConstraint(["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
            name="fk_jobs_seller_conversation_id", deferrable=True, initially="DEFERRED"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    conversation_id: Mapped[str] = mapped_column(String, ForeignKey("conversations.id"), nullable=False, index=True)
    approved_revision_id: Mapped[str] = mapped_column(String, nullable=False)
    listing_snapshot: Mapped[Any] = mapped_column(JSON, nullable=False)
    status: Mapped[str | None] = mapped_column(String, default="queued", nullable=True)
    current_step: Mapped[str | None] = mapped_column(String, nullable=True)
    vendoo_item_id: Mapped[str | None] = mapped_column(String, nullable=True)
    vendoo_url: Mapped[str | None] = mapped_column(String, nullable=True)
    attempt_count: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    """The last Vendoo status Studio saw for a job — one row, status only.

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
