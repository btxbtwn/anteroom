"""Named seller-selected reusable listing fields."""
from sqlalchemy import Column, DateTime, Integer, JSON, String

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow


class ListingTemplate(Base):
    __tablename__ = "listing_templates"

    id = Column(String, primary_key=True, default=new_id)
    name = Column(String, nullable=False)
    fields = Column(JSON, nullable=False)
    revision = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
