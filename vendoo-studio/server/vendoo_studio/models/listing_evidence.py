"""Seller corrections and sale observations, separate from editable listings."""
from sqlalchemy import ForeignKeyConstraint, Column, DateTime, ForeignKey, JSON, String, UniqueConstraint

from vendoo_studio.database import Base
from vendoo_studio.models.ownership import SellerOwned
from vendoo_studio.models.conversation import new_id, utcnow


class ListingCorrection(SellerOwned, Base):
    __tablename__ = "listing_corrections"
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
            name="fk_listing_corrections_seller_conversation_id", deferrable=True, initially="DEFERRED"),
    )

    id = Column(String, primary_key=True, default=new_id)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False, index=True)
    revision_id = Column(String, nullable=False)
    source = Column(String, nullable=False, default="user_form")
    category_path = Column(String, nullable=False)
    brand = Column(String, nullable=False)
    changes = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class SaleSnapshot(SellerOwned, Base):
    __tablename__ = "sale_snapshots"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sale_key"),
        ForeignKeyConstraint(["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
            name="fk_sale_snapshots_seller_conversation_id", deferrable=True, initially="DEFERRED"),
    )

    id = Column(String, primary_key=True, default=new_id)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False, index=True)
    sale_key = Column(String, nullable=False)
    listing = Column(JSON, nullable=False)
    source = Column(String, nullable=False)
    observed_at = Column(DateTime, nullable=False, default=utcnow)

