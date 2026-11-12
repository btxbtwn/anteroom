"""Global operator mail accounting; seller roles have no access."""
from typing import Any

from sqlalchemy import JSON, Integer
from sqlalchemy.orm import Mapped, mapped_column

from vendoo_studio.database import Base


class OperatorEmailState(Base):
    __tablename__ = 'error_email_state'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
