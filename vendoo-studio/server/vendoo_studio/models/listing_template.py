"""Named seller-selected reusable listing fields."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow
from vendoo_studio.models.ownership import SellerOwned


class ListingTemplate(SellerOwned, Base):
    __tablename__ = "listing_templates"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    fields: Mapped[object] = mapped_column(JSON, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
