"""Single-use photo upload links an MCP client asks for (``create_photo_upload_url``).

Each row lets exactly one photo be uploaded to one listing of one seller,
until ``expires_at``. Only the SHA-256 digest of the link's secret is kept;
``id`` is not secret and appears in the upload URL.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from anteroom.database import Base
from anteroom.models.conversation import utcnow
from anteroom.models.ownership import SellerOwned


class McpPhotoUpload(SellerOwned, Base):
    __tablename__ = "mcp_photo_uploads"
    __table_args__ = (
        Index("ix_mcp_photo_uploads_owner_expires", "user_id", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, nullable=False)
    # Not a foreign key: a link outlives nothing but its 15 minutes, and the
    # upload checks the listing still exists before it saves.
    conversation_id: Mapped[str] = mapped_column(String, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
