from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow
from vendoo_studio.models.ownership import SellerOwned


class SourceBox(SellerOwned, Base):
    """A wholesale box the seller bought. Listings point at it through
    ``Conversation.box_id``, so what the box sold for can be set against what
    it cost."""

    __tablename__ = "source_boxes"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_source_boxes_seller_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    store: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    shipping: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # The carrier's list rate the buy list estimated from when the box was
    # bought, so what the store actually charged can tune later estimates.
    list_shipping: Mapped[float | None] = mapped_column(Float, nullable=True)
    pieces: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Pieces that arrived not worth listing: stained, torn, wrong item. Their
    # cost lands on the pieces that can sell.
    unsellable: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # What the buy list expected one piece to sell for when the box was bought,
    # so real sales can show how far off the estimates run.
    estimate_per_piece: Mapped[float | None] = mapped_column(Float, nullable=True)
    bought_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, nullable=True)

    @property
    def landed(self) -> float:
        """What the box cost, delivered."""
        return (self.price or 0.0) + (self.shipping or 0.0)

    @property
    def sellable(self) -> int | None:
        """Pieces worth listing, once the piece count is known."""
        if not self.pieces:
            return None
        return max(0, self.pieces - (self.unsellable or 0))
