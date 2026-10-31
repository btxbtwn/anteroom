"""Owner-scoped Vault references. Secret values live only in Supabase Vault."""

from __future__ import annotations

import uuid

from sqlalchemy import String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from vendoo_studio.database import Base
from vendoo_studio.models.ownership import SellerOwned


class UserSecretReference(SellerOwned, Base):
    __tablename__ = 'user_secret_references'
    __table_args__ = (UniqueConstraint('user_id', 'account', name='uq_user_secret_account'),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex, nullable=False)
    account: Mapped[str] = mapped_column(String(100), nullable=False)
    secret_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
