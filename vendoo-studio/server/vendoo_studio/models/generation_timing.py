"""How long each stage of one listing generation took."""

from sqlalchemy import JSON, Column, DateTime, Float, String

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow


class GenerationTiming(Base):
    """One generate run: seconds per stage, written when the chat stream ends
    and again when marketplace fields finish after it.

    ``stages`` maps a stage name to seconds, in the order the stages ended.
    Stages that run side by side (categories and sold comps) each record their
    own time, so they overlap; ``total_sec`` is wall time from the start of the
    run to the last stage that ended. No foreign key: a timing is history and
    nothing reads it back into a listing.
    """

    __tablename__ = "generation_timings"

    id = Column(String, primary_key=True, default=new_id)
    conversation_id = Column(String, nullable=False, index=True)
    # done | failed | cancelled — how the chat stream ended. The field fill
    # after it adds stages but does not change this.
    outcome = Column(String, nullable=False)
    stages = Column(JSON, nullable=False)
    total_sec = Column(Float, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
