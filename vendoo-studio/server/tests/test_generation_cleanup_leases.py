"""Revoked draft writes remain fenced without becoming crash alerts."""

import asyncio
import logging
from unittest.mock import MagicMock, patch

import pytest

from vendoo_studio.services import generation_timing, listing_generation
from vendoo_studio.services.background_tasks import TaskLeaseExpired, TaskStopped
from vendoo_studio.services.streaming import GenerationRun


@pytest.mark.parametrize("operation", ["record", "append"])
@pytest.mark.parametrize("error", [TaskLeaseExpired, TaskStopped, RuntimeError])
def test_timing_save_classifies_rejected_lease(operation, error, caplog):
    db = MagicMock()
    db.commit.side_effect = error()
    db.get.return_value.stages = {}
    db.get.return_value.total_sec = 1.0
    with patch.object(generation_timing, "SessionLocal", return_value=db):
        if operation == "record":
            assert generation_timing.record_run("draft", {}, total_sec=1, outcome="cancelled") is None
        else:
            generation_timing.add_stages("timing", {"finish": 1}, total_sec=1, outcome="cancelled")
    db.commit.assert_called_once()
    db.rollback.assert_called_once()
    db.close.assert_called_once()
    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert bool(errors) is (error is RuntimeError)


@pytest.mark.parametrize("error", [TaskLeaseExpired, TaskStopped, RuntimeError])
def test_cancelled_generation_keeps_cancellation_when_status_write_is_rejected(error, caplog):
    db = MagicMock()
    db.__enter__.return_value = db
    repo = MagicMock()
    repo.update_status.side_effect = error()
    with (
        patch("vendoo_studio.database.SessionLocal", return_value=db),
        patch.object(listing_generation, "ConversationRepo", return_value=repo),
        patch.object(listing_generation, "load_skill_rules", side_effect=asyncio.CancelledError()),
        patch.object(listing_generation, "comps_search_available", return_value=False),
        patch.object(generation_timing, "record_run", return_value=None),
        pytest.raises(asyncio.CancelledError),
    ):
        asyncio.run(listing_generation.run_listing_generation(
            GenerationRun(), conv_id="draft", provider=None, vision_provider=None,
            vision_name="test", vision_model="test", notes="", item_details="",
            seller_answers="", paths=[], photo_count=0, analysis_fingerprint="",
        ))
    repo.update_status.assert_called_once_with("draft", "draft")
    # The status write ran in its own short session, which closed.
    db.__exit__.assert_called_once()
    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert bool(errors) is (error is RuntimeError)
