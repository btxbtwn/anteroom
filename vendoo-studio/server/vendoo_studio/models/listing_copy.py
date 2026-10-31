"""Completed local copy requests, retained so retries cannot duplicate inventory."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import utcnow
from vendoo_studio.models.ownership import SellerOwned


class ListingCopyReceipt(SellerOwned, Base):
    __tablename__ = "listing_copy_receipts"

    __table_args__ = (
        PrimaryKeyConstraint("user_id", "request_id"),
        ForeignKeyConstraint(["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
            name="fk_listing_copy_receipts_seller_conversation_id", deferrable=True, initially="DEFERRED"),
    )

    request_id: Mapped[str] = mapped_column(String, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    source_kind: Mapped[str] = mapped_column(String, nullable=False, server_default="listing")
    source_id: Mapped[str] = mapped_column(String, nullable=False)
    # Deleting a copy keeps its receipt: retrying the old request must not recreate it.
    conversation_id: Mapped[str | None] = mapped_column(String, ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
