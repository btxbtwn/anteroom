"""Durable seller preferences for hosted accounts. Never stores credentials."""
import uuid

from sqlalchemy import Column, JSON, String, UniqueConstraint

from vendoo_studio.database import Base
from vendoo_studio.models.ownership import SellerOwned


class UserPreference(SellerOwned, Base):
    __tablename__ = 'user_preferences'
    __table_args__ = (UniqueConstraint('user_id', 'key', name='uq_user_preferences_seller_key'),)
    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    key = Column(String(100), nullable=False)
    value = Column(JSON, nullable=False)
