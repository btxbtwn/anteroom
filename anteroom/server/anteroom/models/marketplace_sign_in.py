"""An eBay or Etsy sign-in the seller started and has not finished yet.

Kept in the database, not in the process that started it, so the browser can
finish it on any process: after a hosted deploy, or on another web Machine.
One row per seller and marketplace; starting again replaces it.

Nothing here can collect the seller's tokens. ``state`` is the relay's opaque
sign-in id, also visible in ``authorize_url``; the relay hands the tokens over
only with the one-time claim it gives the seller's browser, which is never
stored. ``finished_at`` is set once, by whichever finish (or failure) gets
there first, so a state cannot be finished twice.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, PrimaryKeyConstraint, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from anteroom.database import Base
from anteroom.models.ownership import SellerOwned


class MarketplaceSignIn(SellerOwned, Base):
    __tablename__ = "marketplace_sign_ins"
    __table_args__ = (PrimaryKeyConstraint("user_id", "marketplace", name="pk_marketplace_sign_ins"),)

    marketplace: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(200), nullable=False)
    authorize_url: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
