
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow
from vendoo_studio.models.ownership import SellerOwned


class CategorySchema(SellerOwned, Base):
    __tablename__ = "category_schemas"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    general_path: Mapped[str] = mapped_column(String, nullable=False, index=True)
    marketplace: Mapped[str] = mapped_column(String, nullable=False)
    category_path: Mapped[str] = mapped_column(String, nullable=False)
    fields: Mapped[object] = mapped_column(JSON, nullable=False)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=True)
    __table_args__ = (UniqueConstraint("user_id", "general_path", "marketplace", name="uq_category_schemas_seller"),)


class CategoryFieldSchema(SellerOwned, Base):
    """Vendoo's own field list for one marketplace category, cached.

    Keyed by the marketplace's own leaf id rather than a Vendoo general path:
    that is what ``/api/category/specifics`` answers on, and two general paths
    can resolve to the same leaf.
    """

    __tablename__ = "category_field_schemas"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    marketplace: Mapped[str] = mapped_column(String, nullable=False)
    category_id: Mapped[str] = mapped_column(String, nullable=False)
    fields: Mapped[object] = mapped_column(JSON, nullable=False)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=True)
    __table_args__ = (UniqueConstraint("user_id", "marketplace", "category_id", name="uq_category_field_schemas_seller"),)


class CategoryNode(SellerOwned, Base):
    __tablename__ = "category_nodes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    marketplace: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str] = mapped_column(String, nullable=False)
    parent_path: Mapped[str] = mapped_column(String, nullable=False, default="")
    label: Mapped[str] = mapped_column(String, nullable=False)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=True)
    __table_args__ = (UniqueConstraint("user_id", "marketplace", "path", name="uq_category_nodes_seller"),)


class CategoryTree(Base):
    __tablename__ = "category_trees"

    marketplace: Mapped[str] = mapped_column(String, primary_key=True, nullable=False)
    roots_loaded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=True)


class CategoryTreeNode(Base):
    __tablename__ = "category_tree_nodes"

    marketplace: Mapped[str] = mapped_column(String, primary_key=True, nullable=False)
    category_id: Mapped[str] = mapped_column(String, primary_key=True, nullable=False)
    parent_id: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str] = mapped_column(String, nullable=False, index=True)
    label: Mapped[str] = mapped_column(String, nullable=False)
    is_leaf: Mapped[bool] = mapped_column(Boolean, nullable=False)
    has_children: Mapped[bool] = mapped_column(Boolean, nullable=False)
    children_loaded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
