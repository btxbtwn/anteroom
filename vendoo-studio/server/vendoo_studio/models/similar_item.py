"""What the similar-item finder remembers between scans.

Photo fingerprints are keyed by the file's checksum, so the same picture on two
items is hashed once and an edited photo gets a fingerprint of its own. Reviews
are the seller's decisions on a pair, so a dismissed or merged pair is not
suggested again.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow
from vendoo_studio.models.ownership import SellerOwned


class PhotoFingerprint(SellerOwned, Base):
    __tablename__ = "photo_fingerprints"

    __table_args__ = (PrimaryKeyConstraint("user_id", "checksum"),)

    checksum: Mapped[str] = mapped_column(String, nullable=False)
    # 64-bit difference hash as 16 hex digits; null when the file could not be read.
    dhash: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, nullable=True)


class SimilarItemReview(SellerOwned, Base):
    __tablename__ = "similar_item_reviews"
    __table_args__ = (
        UniqueConstraint("first_id", "second_id", name="uq_similar_item_reviews_pair"),
        ForeignKeyConstraint(["user_id", "first_id"], ["conversations.user_id", "conversations.id"],
            name="fk_similar_item_reviews_seller_first_id", deferrable=True, initially="DEFERRED"),
        ForeignKeyConstraint(["user_id", "second_id"], ["conversations.user_id", "conversations.id"],
            name="fk_similar_item_reviews_seller_second_id", deferrable=True, initially="DEFERRED"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    # The pair in sorted order, so either way round finds the same row.
    first_id: Mapped[str] = mapped_column(String, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    second_id: Mapped[str] = mapped_column(String, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    # dismissed or merged
    decision: Mapped[str] = mapped_column(String, nullable=False)
    kept_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, nullable=True)
