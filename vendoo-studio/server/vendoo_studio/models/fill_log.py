from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow
from vendoo_studio.models.ownership import SellerOwned


class FillLogEntry(SellerOwned, Base):
    __tablename__ = "fill_log_entries"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    job_id: Mapped[str] = mapped_column(String, ForeignKey("jobs.id"), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    step: Mapped[str] = mapped_column(String, nullable=False)
    marketplace: Mapped[str] = mapped_column(String, nullable=False, index=True)
    field: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    selector: Mapped[str | None] = mapped_column(String, nullable=True)
    value_preview: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, nullable=True)

    __table_args__ = (
        Index("ix_fill_log_job_step", "job_id", "step"),
        ForeignKeyConstraint(["user_id", "job_id"], ["jobs.user_id", "jobs.id"],
            name="fk_fill_log_entries_seller_job_id", deferrable=True, initially="DEFERRED"),
    )
