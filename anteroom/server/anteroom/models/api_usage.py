"""Per-seller managed OpenRouter and Photoroom API usage for beta invoicing."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from anteroom.database import Base
from anteroom.models.conversation import new_id, utcnow
from anteroom.models.ownership import SellerOwned


class ApiUsageEvent(SellerOwned, Base):
    """One billable managed API call charged to Anteroom's OpenRouter or Photoroom account.

    Seller-owned keys are not recorded here: those bills go to the seller's own
    provider accounts. Rows are diagnostics for the operator; nothing reads them
    back into a listing.
    """

    __tablename__ = "api_usage_events"
    __table_args__ = (
        Index("ix_api_usage_events_owner_created", "user_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    model_or_edit: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    cost_source: Mapped[str] = mapped_column(String(32), nullable=False)
    units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
