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
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow
from vendoo_studio.models.ownership import SellerOwned


class DiagnosticRun(SellerOwned, Base):
    __tablename__ = "diagnostic_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    __table_args__ = (
        UniqueConstraint("user_id", "observation_id", name="uq_diagnostic_runs_seller_observation"),
        UniqueConstraint("user_id", "id", name="uq_diagnostic_runs_seller_id"),
        ForeignKeyConstraint(["user_id", "job_id"], ["jobs.user_id", "jobs.id"],
            name="fk_diagnostic_runs_seller_job_id", deferrable=True, initially="DEFERRED"),
    )
    observation_id: Mapped[str] = mapped_column(String, nullable=False)
    job_id: Mapped[str] = mapped_column(String, ForeignKey("jobs.id"), nullable=False, index=True)
    step: Mapped[str] = mapped_column(String, nullable=False)
    collector_version: Mapped[str] = mapped_column(String, nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    timestamp: Mapped[str | None] = mapped_column(String, nullable=True)
    field_count: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    dropdown_count: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    dropdowns_with_options: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    live_dropdowns_with_options: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    total_dropdown_options: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    expanded_sections: Mapped[Any] = mapped_column(JSON, nullable=True)
    headings: Mapped[Any] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, nullable=True)


class FieldObservation(SellerOwned, Base):
    __tablename__ = "field_observations"
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "diagnostic_run_id"], ["diagnostic_runs.user_id", "diagnostic_runs.id"],
            name="fk_field_observations_seller_diagnostic_run_id", deferrable=True, initially="DEFERRED"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    diagnostic_run_id: Mapped[str] = mapped_column(String, ForeignKey("diagnostic_runs.id"), nullable=False, index=True)
    observation_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    label_sources: Mapped[Any] = mapped_column(JSON, nullable=True)
    section_path: Mapped[Any] = mapped_column(JSON, nullable=True)
    selector: Mapped[str | None] = mapped_column(String, nullable=True)
    tag: Mapped[str | None] = mapped_column(String, nullable=True)
    control_type: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    control_id: Mapped[str | None] = mapped_column(String, nullable=True)
    placeholder: Mapped[str | None] = mapped_column(String, nullable=True)
    classes: Mapped[str | None] = mapped_column(String, nullable=True)
    is_dropdown: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    option_count: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    options: Mapped[Any] = mapped_column(JSON, nullable=True)
    options_source: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, nullable=True)
