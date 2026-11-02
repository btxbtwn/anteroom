from __future__ import annotations

from datetime import datetime, UTC
from typing import Any

from pydantic import BaseModel, Field


class ProtocolMessage(BaseModel):
    version: int = 1
    type: str
    message_id: str = Field(default_factory=lambda: __import__("uuid").uuid4().hex[:12])
    job_id: str | None = None
    sent_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    payload: dict[str, Any] = Field(default_factory=dict)
