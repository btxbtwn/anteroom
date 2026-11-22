"""Seller-scoped personal access tokens for the Streamable HTTP MCP server.

Plaintext tokens are shown once on creation and never stored: only the SHA-256
digest and a short non-secret prefix live in this table.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from anteroom.database import Base
from anteroom.models.conversation import new_id, utcnow
from anteroom.models.ownership import SellerOwned


class McpAccessToken(SellerOwned, Base):
    __tablename__ = "mcp_access_tokens"
    __table_args__ = (
        Index("ix_mcp_access_tokens_token_hash", "token_hash", unique=True),
        Index("ix_mcp_access_tokens_owner_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(24), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
