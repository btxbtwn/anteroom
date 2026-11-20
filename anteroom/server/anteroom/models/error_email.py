"""Global operator mail accounting; seller roles have no access."""
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, CheckConstraint, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from anteroom.database import Base


class OperatorEmailState(Base):
    __tablename__ = 'error_email_state'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    group_sync_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class OperatorEmailGroup(Base):
    __tablename__ = 'error_email_groups'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    due: Mapped[float | None] = mapped_column(Float, nullable=True)
    warning: Mapped[bool] = mapped_column(Boolean, nullable=False)
    last_sent: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (Index('ix_error_email_groups_due', 'warning', 'due'),
                      CheckConstraint('count >= 0', name='ck_error_email_group_count'),)
