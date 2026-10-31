from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow
from vendoo_studio.models.ownership import SellerOwned


class FieldRegistry(SellerOwned, Base):
    __tablename__ = "field_registry"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    marketplace: Mapped[str] = mapped_column(String, nullable=False, index=True)
    category_path: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    normalized_label: Mapped[str] = mapped_column(String, nullable=False)
    control_type: Mapped[str | None] = mapped_column(String, nullable=True)
    is_dropdown: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    known_selectors: Mapped[object | None] = mapped_column(JSON, default=[], nullable=True)
    known_options: Mapped[object | None] = mapped_column(JSON, default=[], nullable=True)
    # How known_options was last captured. An authoritative read of an open
    # dropdown replaces the option set; anything weaker only unions into it.
    options_source: Mapped[str | None] = mapped_column(String, nullable=True)
    observation_count: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "user_id", "marketplace", "category_path", "normalized_label",
            name="uq_field_registry_identity"
        ),
    )
