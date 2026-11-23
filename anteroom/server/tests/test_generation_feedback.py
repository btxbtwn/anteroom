import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from anteroom.database import Base, load_models
from anteroom.models.conversation import Conversation
from anteroom.models.listing import ListingRevision
from anteroom.services.generation_feedback import FeedbackInput, feedback_for, feedback_prompt, feedback_summary, save_feedback


def test_feedback_rates_the_generated_revision_and_uses_only_tags_in_prompts():
    load_models()
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        conversation = Conversation(title="Draft")
        db.add(conversation)
        db.flush()
        generated = ListingRevision(conversation_id=conversation.id, listing_json={}, source="model")
        manual = ListingRevision(conversation_id=conversation.id, listing_json={}, source="user_form")
        db.add_all([generated, manual])
        db.commit()
        assert feedback_for(db, conversation.id) == {"revision_id": generated.id, "feedback": None}
        with pytest.raises(HTTPException) as error:
            save_feedback(db, conversation.id, FeedbackInput(revision_id=manual.id, rating="up"))
        assert error.value.status_code == 409
        save_feedback(db, conversation.id, FeedbackInput(
            revision_id=generated.id, rating="down", issues=["flaw", "flaw"], note="PRIVATE NOTE",
        ))
        assert feedback_summary(db) == {"up": 0, "down": 1, "issues": {"flaw": 1}}
        prompt = feedback_prompt(db)
        assert "visible defects" in prompt
        assert "PRIVATE NOTE" not in prompt
        save_feedback(db, conversation.id, FeedbackInput(revision_id=generated.id, rating="up"))
        assert feedback_summary(db) == {"up": 1, "down": 0, "issues": {}}
        assert feedback_prompt(db) == ""

        # L10: feedback older than recent_days is excluded in SQL.
        from datetime import UTC, datetime, timedelta
        old_rev = ListingRevision(
            conversation_id=conversation.id,
            listing_json={},
            source="model",
            generation_feedback={
                "rating": "down",
                "issues": ["price"],
                "note": "",
                "rated_at": (datetime.now(UTC) - timedelta(days=45)).isoformat(),
            },
        )
        db.add(old_rev)
        db.commit()
        assert feedback_summary(db, recent_days=30) == {"up": 1, "down": 0, "issues": {}}
        assert feedback_summary(db, recent_days=60) == {"up": 1, "down": 1, "issues": {"price": 1}}
    engine.dispose()
