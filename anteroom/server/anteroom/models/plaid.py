"""Seller-owned Plaid Items and reconciliation history."""
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from anteroom.database import Base
from anteroom.models.conversation import new_id
from anteroom.models.ownership import SellerOwned


class PlaidConnection(SellerOwned, Base):
    __tablename__ = "plaid_connections"
    __table_args__ = (UniqueConstraint("user_id", "institution_id", name="uq_plaid_owner_institution"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    institution_id: Mapped[str] = mapped_column(String)
    institution_name: Mapped[str] = mapped_column(String)
    environment: Mapped[str] = mapped_column(String)
    accounts: Mapped[list[dict]] = mapped_column(JSON)
    cursor: Mapped[str | None] = mapped_column(Text)
    sync_version: Mapped[int] = mapped_column(Integer, server_default="0")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_attempted_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    history_complete: Mapped[bool] = mapped_column(server_default=false())
    disconnecting: Mapped[bool] = mapped_column(server_default=false())


class PlaidTransaction(SellerOwned, Base):
    __tablename__ = "plaid_transactions"
    __table_args__ = (UniqueConstraint("user_id", "import_key", name="uq_plaid_owner_import"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    import_key: Mapped[str] = mapped_column(String(64))
    # A missing expense at a recorded revision means the seller deleted it.
    # Plaid removals clear the revision, allowing Plaid to restore the entry.
    imported_revision: Mapped[int | None] = mapped_column(Integer)
