"""Direct order-line ledger and seller-reviewed sale alerts. No buyer details."""
from __future__ import annotations

from sqlalchemy import ForeignKeyConstraint, JSON, Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint

from vendoo_studio.database import Base
from vendoo_studio.models.ownership import SellerOwned
from vendoo_studio.models.conversation import new_id, utcnow

SALE_STATES = ("new", "dismissed")
ORDER_STATUSES = ("paid", "partially_refunded", "cancelled", "refunded", "cancellation_pending")
PAID_STATUSES = ("paid", "partially_refunded")
# Who reported the sale: a marketplace (webhook, order read, Chrome) or the
# seller through Mark as sold. "other" is a sale off every marketplace.
SALE_SOURCES = ("marketplace", "seller")
OTHER_MARKETPLACE = "other"


class MarketplaceSale(SellerOwned, Base):
    __tablename__ = "marketplace_sales"
    __table_args__ = (
        UniqueConstraint("user_id", "marketplace", "order_id", "line_id", name="uq_marketplace_sales_order_line"),
        Index("ix_marketplace_sales_state", "state"),
        ForeignKeyConstraint(["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
            name="fk_marketplace_sales_seller_conversation_id", deferrable=True, initially="DEFERRED"),
    )

    id = Column(String, primary_key=True, default=new_id)
    marketplace = Column(String, nullable=False)
    # eBay order ID / Etsy receipt ID, and the line within it.
    order_id = Column(String, nullable=False)
    line_id = Column(String, nullable=False)
    # The marketplace's own listing ID (eBay item ID, Etsy listing_id).
    listing_id = Column(String, nullable=True)
    quantity = Column(Integer, nullable=True)
    order_status = Column(String, nullable=False, default="paid", server_default="paid")
    order_updated_at = Column(DateTime, nullable=True)
    order_observed_at = Column(DateTime, nullable=True)
    financials = Column(JSON, nullable=True)
    financial_revision = Column(Integer, nullable=False, default=0, server_default="0")
    # The local item matched by its validated marketplace listing identity.
    conversation_id = Column(String, ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True)
    state = Column(String, nullable=False, default="new")
    sold_at = Column(DateTime, nullable=True)
    received_at = Column(DateTime, nullable=False, default=utcnow)
    resolved_at = Column(DateTime, nullable=True)
    source = Column(String, nullable=False, default="marketplace", server_default="marketplace")
