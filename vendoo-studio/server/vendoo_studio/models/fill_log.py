from __future__ import annotations

from sqlalchemy import ForeignKeyConstraint, Column, String, DateTime, ForeignKey, Index

from vendoo_studio.database import Base
from vendoo_studio.models.ownership import SellerOwned
from vendoo_studio.models.conversation import new_id, utcnow


class FillLogEntry(SellerOwned, Base):
    __tablename__ = "fill_log_entries"

    id = Column(String, primary_key=True, default=new_id)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False, index=True)
    conversation_id = Column(String, nullable=False, index=True)
    step = Column(String, nullable=False)
    marketplace = Column(String, nullable=False, index=True)
    field = Column(String, nullable=False)
    status = Column(String, nullable=False)
    reason = Column(String, nullable=True)
    selector = Column(String, nullable=True)
    value_preview = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_fill_log_job_step", "job_id", "step"),
        ForeignKeyConstraint(["user_id", "job_id"], ["jobs.user_id", "jobs.id"],
            name="fk_fill_log_entries_seller_job_id", deferrable=True, initially="DEFERRED"),
    )
