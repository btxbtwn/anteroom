"""Seller-maintained promotion plans. These records never trigger automation."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow
from vendoo_studio.models.ownership import SellerOwned


class SaleEvent(SellerOwned, Base):
    __tablename__ = "sale_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    marketplace: Mapped[str] = mapped_column(String, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    timezone: Mapped[str] = mapped_column(String, nullable=False)
    discount_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    shipping_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    minimum_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Snapshot the chosen items so an edit or deletion cannot rewrite history.
    items: Mapped[object] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="planned")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
