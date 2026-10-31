"""Durable draft-only work. Marketplace mutations never enter this queue."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

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

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    conversation_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default='queued')
    payload: Mapped[Any] = mapped_column(JSON, nullable=False, default=dict)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class BackgroundTaskEvent(SellerOwned, Base):
    __tablename__ = 'background_task_events'
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'task_id'], ['background_tasks.user_id', 'background_tasks.id'],
                             name='fk_background_task_events_seller_task', deferrable=True, initially='DEFERRED'),
        UniqueConstraint('task_id', 'sequence', name='uq_background_task_event_sequence'),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    task_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[str] = mapped_column(Text, nullable=False)
