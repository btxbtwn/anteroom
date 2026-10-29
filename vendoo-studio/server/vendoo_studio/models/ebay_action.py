"""Receipts and fingerprints only; no buyer details or message bodies."""
from sqlalchemy import Column, DateTime, Index, String, text

from vendoo_studio.database import Base
from vendoo_studio.models.ownership import SellerOwned
from vendoo_studio.models.conversation import new_id, utcnow


class EbayAction(SellerOwned, Base):
    __tablename__ = "ebay_actions"
    __table_args__ = (
        Index("ix_ebay_actions_fingerprint", "account_id", "fingerprint"),
        # One eBay workspace write at a time, even from concurrent requests.
        Index(
            "uq_ebay_action_in_flight", "state", unique=True,
            sqlite_where=text("state = 'sending'"), postgresql_where=text("state = 'sending'"),
        ),
    )

    id = Column(String, primary_key=True, default=new_id)
    account_id = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    resource_id = Column(String, nullable=False)
    fingerprint = Column(String, nullable=False)
    state = Column(String, nullable=False, default="preview")
    receipt_id = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    expires_at = Column(DateTime, nullable=False)
