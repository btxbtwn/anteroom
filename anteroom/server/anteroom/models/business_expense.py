"""Seller-owned bookkeeping records. Monetary amounts are stored in cents."""
from datetime import date
from decimal import Decimal

from sqlalchemy import BigInteger, Date, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from anteroom.database import Base
from anteroom.models.conversation import new_id
from anteroom.models.ownership import SellerOwned


class BusinessExpense(SellerOwned, Base):
    __tablename__ = "business_expenses"
    __table_args__ = (
        Index("ix_business_expenses_owner_date", "user_id", "incurred_date", "id"),
        Index("ux_business_expenses_owner_import", "user_id", "import_key", unique=True),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    __mapper_args__ = {"version_id_col": revision}

    incurred_date: Mapped[date] = mapped_column(Date)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), server_default="USD")
    category: Mapped[str] = mapped_column(String(32))
    vendor: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(String(512))
    notes: Mapped[str] = mapped_column(Text)
    accounting_type: Mapped[str] = mapped_column(String(24))
    distance: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    distance_unit: Mapped[str | None] = mapped_column(String(2))
    mileage_rate_cents: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    origin_destination: Mapped[str | None] = mapped_column(String(512))
    import_source: Mapped[str] = mapped_column(String(8), server_default="manual")
    import_key: Mapped[str | None] = mapped_column(String(64))
