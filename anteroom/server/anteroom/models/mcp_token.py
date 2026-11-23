"""Seller-scoped personal access tokens for the Streamable HTTP MCP server.

Plaintext tokens are shown once on creation and never stored: only the SHA-256
digest and a short non-secret prefix live in this table. ``permissions`` holds
what the seller lets the token reach, per area (see ``services/mcp_tokens.py``).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, String, UniqueConstraint
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
    permissions: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class McpOAuthConnection(SellerOwned, Base):
    """An app the seller connected by signing in (Claude, ChatGPT, Gemini).

    Supabase's OAuth server issues the app its tokens; ``client_id`` is that
    app's id there. This row holds what the app may reach and whether the
    seller turned it off. No token is stored.
    """

    __tablename__ = "mcp_oauth_connections"
    __table_args__ = (
        UniqueConstraint("user_id", "client_id", name="uq_mcp_oauth_connections_owner_client"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    client_id: Mapped[str] = mapped_column(String(128), nullable=False)
    client_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    permissions: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When Supabase confirmed the app's sign-in was ended after it was turned off.
    signed_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
