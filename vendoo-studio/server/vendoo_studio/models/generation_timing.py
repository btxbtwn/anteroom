"""How long each stage of one listing generation took."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, String, text
from sqlalchemy.orm import Mapped, mapped_column

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow
from vendoo_studio.models.ownership import SellerOwned


class GenerationTiming(SellerOwned, Base):
    """One generate run: seconds per stage, written when the chat stream ends
    and again when marketplace fields finish after it.

    ``stages`` maps a stage name to seconds, in the order the stages ended.
    Stages that run side by side (categories and sold comps) each record their
    own time, so they overlap; ``total_sec`` is wall time from the start of the
    run to the last stage that ended. No foreign key: a timing is history and
    nothing reads it back into a listing.
    """

    __tablename__ = "generation_timings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    conversation_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # The stream starts preparing; background field work supplies its final outcome.
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    stages: Mapped[object] = mapped_column(JSON, nullable=False)
    calls: Mapped[object] = mapped_column(JSON, nullable=False, default=list, server_default=text("'[]'"))
    total_sec: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
