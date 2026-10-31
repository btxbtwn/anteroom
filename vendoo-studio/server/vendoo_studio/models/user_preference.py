"""Durable seller preferences for hosted accounts. Never stores credentials."""

from __future__ import annotations

import uuid

from sqlalchemy import JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from vendoo_studio.database import Base
from vendoo_studio.models.ownership import SellerOwned


class UserPreference(SellerOwned, Base):
    __tablename__ = 'user_preferences'
    __table_args__ = (UniqueConstraint('user_id', 'key', name='uq_user_preferences_seller_key'),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex, nullable=False)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[object] = mapped_column(JSON, nullable=False)
