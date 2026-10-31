"""Ad spend the seller copied from a marketplace's ads dashboard."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow
from vendoo_studio.models.ownership import SellerOwned


class AdSpend(SellerOwned, Base):
    """What one ads programme cost over a stretch of days, and what its own
    dashboard says it sold. Poshmark Promoted Closet and Etsy Ads have no API
    Studio can read, so these figures are typed in, never fetched."""

    __tablename__ = "ad_spend"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    marketplace: Mapped[str] = mapped_column(String, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    spend: Mapped[float] = mapped_column(Float, nullable=False)
    clicks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Orders and revenue the dashboard attributes to the ads; organic sales are not in them.
    orders: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revenue: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Set on rows an uploaded Etsy Ads CSV wrote, so the next upload can replace them.
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
