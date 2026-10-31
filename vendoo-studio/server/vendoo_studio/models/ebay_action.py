"""Receipts and fingerprints only; no buyer details or message bodies."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow
from vendoo_studio.models.ownership import SellerOwned


class EbayAction(SellerOwned, Base):
    __tablename__ = "ebay_actions"
    __table_args__ = (
        Index("ix_ebay_actions_fingerprint", "account_id", "fingerprint"),
        # One eBay workspace write at a time, even from concurrent requests.
        Index(
            "uq_ebay_action_in_flight", "user_id", "state", unique=True,
            sqlite_where=text("state = 'sending'"), postgresql_where=text("state = 'sending'"),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    account_id: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    resource_id: Mapped[str] = mapped_column(String, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False, default="preview")
    receipt_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
