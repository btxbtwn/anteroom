"""Sales a marketplace reported directly, through Anteroom's relay.

Vendoo stays the record of what is listed where. These rows are the faster
signal: "this sold on eBay a moment ago", waiting for the seller to delist it
elsewhere or dismiss it. Nothing here delists on its own.
"""
from __future__ import annotations

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow

SALE_STATES = ("new", "delisted", "dismissed")


class MarketplaceSale(Base):
    __tablename__ = "marketplace_sales"
    __table_args__ = (
        # One row per order line, however often the marketplace repeats itself.
        UniqueConstraint("marketplace", "order_id", "line_id", name="uq_marketplace_sales_order_line"),
        Index("ix_marketplace_sales_state", "state"),
    )

    id = Column(String, primary_key=True, default=new_id)
    marketplace = Column(String, nullable=False)
    # eBay order ID / Etsy receipt ID, and the line within it.
    order_id = Column(String, nullable=False)
    line_id = Column(String, nullable=False)
    # The marketplace's own listing ID (eBay item ID, Etsy listing_id).
    listing_id = Column(String, nullable=True)
    quantity = Column(Integer, nullable=True)
    # The Studio listing it matched, by the listing URL Vendoo reported.
    conversation_id = Column(String, ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True)
    state = Column(String, nullable=False, default="new")
    # Marketplaces the seller asked Vendoo to delist from, and its reply.
    delisted_from = Column(JSON, nullable=True)
    sold_at = Column(DateTime, nullable=True)
    received_at = Column(DateTime, nullable=False, default=utcnow)
    resolved_at = Column(DateTime, nullable=True)
