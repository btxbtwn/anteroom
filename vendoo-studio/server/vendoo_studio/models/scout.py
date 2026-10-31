from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow
from vendoo_studio.models.ownership import SellerOwned


class ScoutCheck(SellerOwned, Base):
    """One "is this worth buying?" check from photos taken while sourcing.

    Kept after the decision, so a check that became a listing can later be set
    against what the item actually sold for."""

    __tablename__ = "scout_checks"
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
            name="fk_scout_checks_seller_conversation_id", deferrable=True, initially="DEFERRED"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="checking")  # checking | done | failed
    asking_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    photos: Mapped[Any] = mapped_column(JSON, nullable=False, default=list)  # stored filenames, in order
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    comps: Mapped[str | None] = mapped_column(Text, nullable=True)
    estimate: Mapped[float | None] = mapped_column(Float, nullable=True)  # expected sale price
    comps_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision: Mapped[str | None] = mapped_column(String, nullable=True)  # bought | passed
    conversation_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
