"""The business assistant's conversations, apart from any listing's chat."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow
from vendoo_studio.models.ownership import SellerOwned

NEW_THREAD_TITLE = "New thread"


class AssistantThread(SellerOwned, Base):
    """One conversation with the business assistant.

    The seller can keep several of these. A listing's chat is never one of them.
    """

    __tablename__ = "assistant_threads"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False, default=NEW_THREAD_TITLE)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, index=True)


class AssistantMemory(SellerOwned, Base):
    """One fact the business assistant keeps about how the seller runs the shop.

    A conversation can be cleared. These stay, and a later turn can rewrite or
    drop one when the seller corrects it. The seller's own note is separate.
    """

    __tablename__ = "assistant_memories"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class AssistantMessage(SellerOwned, Base):
    """One turn of a conversation about the business.

    Only the question and the answer are kept. The figures an answer was built
    from are read fresh from the database on every question, never stored here.
    An answer that proposes changes carries them, and whether the seller applied them."""

    __tablename__ = "assistant_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    thread_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String, nullable=False)  # user | assistant
    text: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    # {"status": proposed | applied | dismissed, "items": [...]}; see services/assistant_changes.py.
    changes: Mapped[Any] = mapped_column(JSON, nullable=True)
    # {"status": proposed | confirmed | dismissed, "proposal": {...}}; see services/automations.py.
    automation: Mapped[Any] = mapped_column(JSON, nullable=True)
    # Images attached to a question; see services/chat_attachments.py.
    attachments: Mapped[Any] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, index=True)
