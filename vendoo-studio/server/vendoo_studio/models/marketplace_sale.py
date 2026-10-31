"""Direct order-line ledger and seller-reviewed sale alerts. No buyer details."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow
from vendoo_studio.models.ownership import SellerOwned

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

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    marketplace: Mapped[str] = mapped_column(String, nullable=False)
    # eBay order ID / Etsy receipt ID, and the line within it.
    order_id: Mapped[str] = mapped_column(String, nullable=False)
    line_id: Mapped[str] = mapped_column(String, nullable=False)
    # The marketplace's own listing ID (eBay item ID, Etsy listing_id).
    listing_id: Mapped[str | None] = mapped_column(String, nullable=True)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order_status: Mapped[str] = mapped_column(String, nullable=False, default="paid", server_default="paid")
    order_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    order_observed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    financials: Mapped[object | None] = mapped_column(JSON, nullable=True)
    financial_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # The local item matched by its validated marketplace listing identity.
    conversation_id: Mapped[str | None] = mapped_column(String, ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True)
    state: Mapped[str] = mapped_column(String, nullable=False, default="new")
    sold_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False, default="marketplace", server_default="marketplace")

    @property
    def is_order_backed(self) -> bool:
        """True for a real order, not a listing a check found marked sold.

        A check that reads a listing as sold has no order of its own: it names
        the listing as both order and line. A marketplace order, a webhook and
        the seller's Mark as sold all carry their own order or line identity,
        or recorded amounts.
        """
        return bool(self.financials or (self.order_id and self.order_id != self.line_id))
