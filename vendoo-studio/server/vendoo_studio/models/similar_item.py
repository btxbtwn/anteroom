"""What the similar-item finder remembers between scans.

Photo fingerprints are keyed by the file's checksum, so the same picture on two
items is hashed once and an edited photo gets a fingerprint of its own. Reviews
are the seller's decisions on a pair, so a dismissed or merged pair is not
suggested again.
"""
from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow


class PhotoFingerprint(Base):
    __tablename__ = "photo_fingerprints"

    checksum = Column(String, primary_key=True)
    # 64-bit difference hash as 16 hex digits; null when the file could not be read.
    dhash = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class SimilarItemReview(Base):
    __tablename__ = "similar_item_reviews"
    __table_args__ = (UniqueConstraint("first_id", "second_id", name="uq_similar_item_reviews_pair"),)

    id = Column(String, primary_key=True, default=new_id)
    # The pair in sorted order, so either way round finds the same row.
    first_id = Column(String, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    second_id = Column(String, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    # dismissed or merged
    decision = Column(String, nullable=False)
    kept_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)
