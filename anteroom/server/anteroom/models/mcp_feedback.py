"""Feedback a seller's AI assistant sent to the Anteroom team over MCP.

Each row is kept whether or not the email to the operator went out, so a
failed or skipped send loses nothing. ``message`` is stored after credential
masking (``services/redaction.py``). ``delivery`` records what happened to
the email: ``sending``, ``sent``, ``failed`` or ``not_sent`` (the Mac app,
sandboxes and a paused operator mailbox never send).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from anteroom.database import Base
from anteroom.models.conversation import new_id, utcnow
from anteroom.models.ownership import SellerOwned


class McpFeedback(SellerOwned, Base):
    __tablename__ = "mcp_feedback"
    __table_args__ = (Index("ix_mcp_feedback_owner_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # The listing the feedback is about, as an ID only; never its contents.
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The token or connected app's name, never the token.
    source: Mapped[str] = mapped_column(String(200), nullable=False)
    delivery: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
