"""Durable draft-only work. Marketplace mutations never enter this queue."""
from sqlalchemy import Column, DateTime, ForeignKeyConstraint, Index, Integer, JSON, String, Text, UniqueConstraint

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow
from vendoo_studio.models.ownership import SellerOwned


class BackgroundTask(SellerOwned, Base):
    __tablename__ = 'background_tasks'
    __table_args__ = (
        UniqueConstraint('user_id', 'id', name='uq_background_tasks_seller_id'),
        ForeignKeyConstraint(['user_id', 'conversation_id'], ['conversations.user_id', 'conversations.id'],
                             name='fk_background_tasks_seller_conversation', deferrable=True, initially='DEFERRED'),
        Index('ix_background_tasks_pending', 'status', 'created_at'),
    )

    id = Column(String, primary_key=True, default=new_id)
    conversation_id = Column(String, nullable=False, index=True)
    kind = Column(String(32), nullable=False)
    status = Column(String(20), nullable=False, default='queued')
    payload = Column(JSON, nullable=False, default=dict)
    lease_token = Column(String(36))
    leased_until = Column(DateTime(timezone=True))
    error = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class BackgroundTaskEvent(SellerOwned, Base):
    __tablename__ = 'background_task_events'
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'task_id'], ['background_tasks.user_id', 'background_tasks.id'],
                             name='fk_background_task_events_seller_task', deferrable=True, initially='DEFERRED'),
        UniqueConstraint('task_id', 'sequence', name='uq_background_task_event_sequence'),
    )
    id = Column(String, primary_key=True, default=new_id)
    task_id = Column(String, nullable=False, index=True)
    sequence = Column(Integer, nullable=False)
    data = Column(Text, nullable=False)
