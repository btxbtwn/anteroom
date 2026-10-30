"""Owner-scoped Vault references. Secret values live only in Supabase Vault."""
import uuid

from sqlalchemy import Column, String, UniqueConstraint, Uuid

from vendoo_studio.database import Base
from vendoo_studio.models.ownership import SellerOwned


class UserSecretReference(SellerOwned, Base):
    __tablename__ = 'user_secret_references'
    __table_args__ = (UniqueConstraint('user_id', 'account', name='uq_user_secret_account'),)
    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    account = Column(String(100), nullable=False)
    secret_id = Column(Uuid(as_uuid=False), nullable=False)
