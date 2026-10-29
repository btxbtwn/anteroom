"""Completed local copy requests, retained so retries cannot duplicate inventory."""
from sqlalchemy import Column, DateTime, ForeignKey, String

from vendoo_studio.database import Base
from vendoo_studio.models.ownership import SellerOwned
from vendoo_studio.models.conversation import utcnow


class ListingCopyReceipt(SellerOwned, Base):
    __tablename__ = "listing_copy_receipts"

    request_id = Column(String, primary_key=True)
    fingerprint = Column(String, nullable=False)
    source_kind = Column(String, nullable=False, server_default="listing")
    source_id = Column(String, nullable=False)
    # Deleting a copy keeps its receipt: retrying the old request must not recreate it.
    conversation_id = Column(String, ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
