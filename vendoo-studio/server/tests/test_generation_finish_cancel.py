"""A seller's cancel during the post-generate finish is an expected end, not an error to email."""
import asyncio
import logging

from vendoo_studio.services import listing_generation
from vendoo_studio.services.background_tasks import TaskStopped


def test_a_cancel_during_the_finish_is_not_logged_as_a_failure(monkeypatch, caplog):
    posted, recorded = [], []

    async def worker_session(work, *args):
        if work is listing_generation._finish_inputs:
            return "photos"
        if work is listing_generation._photos_fingerprint:
            return "photos"
        posted.append(work)
        # The cancel ended this task's write lease: every later write is fenced.
        raise TaskStopped()

    async def fill_native_fields(*args, **kwargs):
        raise TaskStopped()

    monkeypatch.setattr(listing_generation, "in_worker_session", worker_session)
    monkeypatch.setattr("vendoo_studio.services.native_field_fill.fill_native_fields", fill_native_fields)
    monkeypatch.setattr(listing_generation.generation_timing, "add_stages",
                        lambda timing_id, stages, **kwargs: recorded.append(kwargs.get("outcome")))
    with caplog.at_level(logging.INFO):
        asyncio.run(listing_generation._finish_generation_background(
            "draft", {"title": "Coat"}, evidence="", provider=object(),
            timing_id="timing", run_started=0.0, expected_revision_id="rev"))
    # Error records reach the operator's error email; a cancel must not.
    assert [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR] == []
    # No "finishing failed, retry generate" message into a draft the task no longer owns.
    assert posted == []
    assert recorded == ["cancelled"]
