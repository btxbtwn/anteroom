"""Seller-enabled scheduled shares and automatic offers to likers, and their rounds.

An automation is saved off and does nothing until the seller turns it on. Any
edit turns it off again. Each send it makes is a ``listing_promotions`` row
carrying its ``automation_id``; each round is a ``promotion_runs`` row.
"""
from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String

from vendoo_studio.database import Base
from vendoo_studio.models.ownership import SellerOwned
from vendoo_studio.models.conversation import new_id, utcnow


class PromotionAutomation(SellerOwned, Base):
    __tablename__ = "promotion_automations"

    id = Column(String, primary_key=True, default=new_id)
    marketplace = Column(String, nullable=False)
    action = Column(String, nullable=False)
    name = Column(String, nullable=False)
    enabled = Column(Boolean, nullable=False, default=False)
    # "all" live listings on the marketplace, or the chosen ``conversation_ids``; never the excluded ones.
    scope = Column(String, nullable=False, default="all")
    conversation_ids = Column(JSON, nullable=False, default=list)
    excluded_ids = Column(JSON, nullable=False, default=list)
    min_days_listed = Column(Integer, nullable=False, default=0)
    # Shares only.
    share_order = Column(String, nullable=True)
    speed = Column(String, nullable=True)
    daily_cap = Column(Integer, nullable=True)
    times = Column(JSON, nullable=True)
    timezone = Column(String, nullable=True)
    # Offers only. No shipping label means no shipping discount.
    discount_percent = Column(Integer, nullable=True)
    min_likes = Column(Integer, nullable=True)
    shipping_label = Column(String, nullable=True)
    paused_reason = Column(String, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    last_run_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class PromotionRun(SellerOwned, Base):
    __tablename__ = "promotion_runs"

    id = Column(String, primary_key=True, default=new_id)
    automation_id = Column(String, ForeignKey("promotion_automations.id", ondelete="CASCADE"), nullable=False, index=True)
    started_at = Column(DateTime, nullable=False, default=utcnow)
    finished_at = Column(DateTime, nullable=True)
    sent = Column(Integer, nullable=False, default=0)
    skipped = Column(Integer, nullable=False, default=0)
    failed = Column(Integer, nullable=False, default=0)
    note = Column(String, nullable=True)
