"""Content-free hosted listing quality observations."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from anteroom.database import Base
from anteroom.models.conversation import utcnow
from anteroom.models.ownership import SellerOwned


class ListingQualityAttempt(SellerOwned, Base):
    __tablename__ = "listing_quality_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
            name="fk_listing_quality_attempts_seller_conversation_id", ondelete="CASCADE",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String, index=True)
    revision_id: Mapped[str] = mapped_column(String, unique=True)
    generated_revision_id: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    corrections: Mapped[dict[str, dict[str, int]]] = mapped_column(JSON, default=dict)
    specific_slots: Mapped[dict[str, dict[str, str]]] = mapped_column(JSON, default=dict)
    overwrite_compared: Mapped[bool] = mapped_column(default=False)
    overwrites: Mapped[dict[str, dict[str, dict[str, int]]]] = mapped_column(JSON, default=dict)
