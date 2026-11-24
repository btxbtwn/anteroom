"""Marketplace listings an import left for the seller to compare with an existing item.

Import all does not duplicate a listing that looks like an item already in
Anteroom: it holds it here, so the import screen on any device and the
seller's assistant read the same queue. A pending row waits for the seller to
link the listing to the item or import it as new. A ``not_match`` row records
that the seller said the two are different products, so that item is not
suggested for that listing again. Rows are only ever about Anteroom's own
inventory; nothing here changes a marketplace.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Float, ForeignKey, ForeignKeyConstraint, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from anteroom.database import Base
from anteroom.models.conversation import new_id, utcnow
from anteroom.models.ownership import SellerOwned

PENDING = "pending"
NOT_MATCH = "not_match"


class ImportReview(SellerOwned, Base):
    __tablename__ = "import_reviews"
    __table_args__ = (
        UniqueConstraint("user_id", "marketplace", "listing_id", "conversation_id", name="uq_import_reviews_listing_item"),
        Index("ix_import_reviews_owner_decision", "user_id", "decision", "created_at"),
        ForeignKeyConstraint(["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
                             name="fk_import_reviews_seller_conversation_id", deferrable=True, initially="DEFERRED"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    marketplace: Mapped[str] = mapped_column(String(32), nullable=False)
    listing_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # What the marketplace showed when the import read it.
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # The existing item the listing looks like.
    conversation_id: Mapped[str] = mapped_column(
        String, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    # How alike the titles read, 0 to 1.
    score: Mapped[float] = mapped_column(Float, nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False, default=PENDING, server_default=PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
