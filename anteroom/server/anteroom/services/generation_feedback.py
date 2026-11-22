"""Seller feedback lives on the generated revision it rates."""

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from anteroom.models.listing import ListingRevision

Issue = Literal["category", "price", "flaw", "size", "style"]
GUIDANCE = {
    "category": "Recheck the exact item type and category.",
    "price": "Verify that pricing evidence matches this item's model and condition.",
    "flaw": "Inspect the supplied photos for visible defects.",
    "size": "Verify size against the readable tag and seller measurements.",
    "style": "Follow the title and description rules for the current item.",
}


class FeedbackInput(BaseModel):
    revision_id: str
    rating: Literal["up", "down"]
    issues: list[Issue] = Field(default_factory=list, max_length=5)
    note: str = Field(default="", max_length=500)


def latest_generated(db: Session, conversation_id: str) -> ListingRevision | None:
    return db.scalar(select(ListingRevision).where(
        ListingRevision.conversation_id == conversation_id,
        ListingRevision.source.in_(("model", "generation_finalize")),
    ).order_by(ListingRevision.created_at.desc(), ListingRevision.id.desc()).limit(1))


def feedback_for(db: Session, conversation_id: str) -> dict | None:
    row = latest_generated(db, conversation_id)
    return {"revision_id": row.id, "feedback": row.generation_feedback} if row else None


def save_feedback(db: Session, conversation_id: str, body: FeedbackInput) -> dict:
    row = latest_generated(db, conversation_id)
    if row is None:
        raise HTTPException(404, "No generated revision found")
    if row.id != body.revision_id:
        raise HTTPException(409, "A newer generation is available. Reload before rating it.")
    row.generation_feedback = {
        "rating": body.rating,
        "issues": list(dict.fromkeys(body.issues)) if body.rating == "down" else [],
        "note": body.note.strip() if body.rating == "down" else "",
        "rated_at": datetime.now(UTC).isoformat(),
    }
    db.commit()
    return row.generation_feedback


def feedback_summary(db: Session, *, recent_days: int = 30) -> dict:
    since = datetime.now(UTC) - timedelta(days=recent_days)
    rows = db.scalars(select(ListingRevision.generation_feedback).where(
        ListingRevision.generation_feedback.is_not(None),
    ))
    ratings: Counter[str] = Counter()
    issues: Counter[str] = Counter()
    for row in rows:
        if not row or row["rated_at"] < since.isoformat():
            continue
        ratings[row["rating"]] += 1
        if row["rating"] == "down":
            issues.update(tag for tag in row["issues"] if tag in GUIDANCE)
    return {"up": ratings["up"], "down": ratings["down"], "issues": dict(issues)}


def feedback_prompt(db: Session) -> str:
    issues = feedback_summary(db)["issues"]
    if not issues:
        return ""
    return "\n\nRecent seller feedback. Apply these checks to this item's evidence:\n" + "\n".join(
        f"- {GUIDANCE[tag]}" for tag in GUIDANCE if tag in issues
    )
