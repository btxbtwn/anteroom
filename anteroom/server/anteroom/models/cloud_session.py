"""Seller-owned metadata, audit trail, and write-ahead journals for cloud marketplace sessions.

Encrypted session cookies live only in Supabase Vault (``cloud-session-<marketplace>``),
never in these tables. ``cloud_marketplace_sessions`` holds non-secret status metadata so
the web process can report status without ever decrypting credentials.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from anteroom.database import Base
from anteroom.models.conversation import new_id, utcnow
from anteroom.models.ownership import SellerOwned

CLOUD_SESSION_STATES = ("pending", "connected", "expired", "revoked", "error")
CLOUD_AUDIT_EVENTS = ("token_minted", "connect", "decrypt", "refresh_cookies", "revoke", "expired")


class CloudMarketplaceSession(SellerOwned, Base):
    __tablename__ = "cloud_marketplace_sessions"
    __table_args__ = (
        UniqueConstraint("user_id", "marketplace", name="uq_cloud_marketplace_sessions_owner_market"),
        Index("ix_cloud_marketplace_sessions_market_state", "marketplace", "state"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    marketplace: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    account_label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Sticky proxy session identifier per seller/marketplace (not a secret).
    proxy_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # SHA-256 hex digest of an active single-use extension upload token; cleared on consume/revoke.
    upload_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    upload_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class CloudSessionAudit(SellerOwned, Base):
    __tablename__ = "cloud_session_audit"
    __table_args__ = (
        Index("ix_cloud_session_audit_owner_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    marketplace: Mapped[str] = mapped_column(String(32), nullable=False)
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class CloudOperationJournal(SellerOwned, Base):
    __tablename__ = "cloud_operation_journals"
    __table_args__ = (
        UniqueConstraint("user_id", "journal_key", name="uq_cloud_operation_journals_owner_key"),
        Index("ix_cloud_operation_journals_owner_market", "user_id", "marketplace"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    marketplace: Mapped[str] = mapped_column(String(32), nullable=False)
    journal_key: Mapped[str] = mapped_column(String(160), nullable=False)
    payload: Mapped[Any] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
