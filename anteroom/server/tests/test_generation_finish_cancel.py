"""A seller's cancel during the post-generate finish is an expected end, not an error to email."""
import asyncio
import logging

from sqlalchemy.orm import Session

from worker_sessions import memory_engine, worker_sessions

from anteroom.database import Base
from anteroom.repositories.queries import ConversationRepo
from anteroom.services import listing_generation, listing_persistence
from anteroom.services.background_tasks import TaskStopped


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
    monkeypatch.setattr("anteroom.services.native_field_fill.fill_native_fields", fill_native_fields)
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


def test_finishing_fills_etsy_attributes_when_etsy_is_selected(monkeypatch):
    summaries, etsy_calls = [], []
    filled = {"title": "Tee", "etsy_specifics": {"category_specifics": {"Sleeve length": "Short sleeve"}}}

    async def worker_session(work, *args):
        if work in (listing_generation._finish_inputs, listing_generation._photos_fingerprint):
            return "photos"
        if work is listing_generation._finish_summary:
            summaries.append(args[1])

    async def fill_native_fields(conv_id, provider, *, evidence, expected_photo_fingerprint):
        assert (conv_id, evidence, expected_photo_fingerprint) == ("draft", "short sleeves", "photos")
        return None

    async def fill_etsy_attributes(conv_id, provider, *, evidence, photos):
        etsy_calls.append((conv_id, evidence, photos))
        return filled

    monkeypatch.setattr(listing_generation, "in_worker_session", worker_session)
    monkeypatch.setattr("anteroom.services.native_field_fill.fill_native_fields", fill_native_fields)
    monkeypatch.setattr("anteroom.services.native_field_fill.fill_etsy_attributes", fill_etsy_attributes)
    monkeypatch.setattr("anteroom.services.marketplaces.get_selected_marketplaces", lambda: ["ebay", "etsy"])
    monkeypatch.setattr(listing_generation.generation_timing, "add_stages", lambda *args, **kwargs: None)
    asyncio.run(listing_generation._finish_generation_background(
        "draft", {"title": "Tee"}, evidence="short sleeves", provider=object(),
        timing_id="timing", run_started=0.0, expected_revision_id="rev"))
    # Answers are fenced on the photos generation read.
    assert etsy_calls == [("draft", "short sleeves", "photos")]
    assert summaries == [filled]


def test_a_photo_added_during_etsy_fill_suppresses_the_completion_summary(monkeypatch):
    engine = memory_engine()
    Base.metadata.create_all(engine)
    listing = {"title": "Tee"}
    outcomes = []
    with Session(engine) as db:
        repo = ConversationRepo(db)
        conv_id = repo.create(title="Tee").id
        revision_id = listing_persistence.save_revision(db, conv_id, listing, source="model").id
        fingerprint = listing_generation.photo_fingerprint(repo.get_photos(conv_id))

    async def fill_native_fields(*args, **kwargs):
        return None

    async def fill_etsy_attributes(*args, **kwargs):
        def add_photo(db):
            ConversationRepo(db).insert_photo(conv_id, "tee.jpg", "tee.jpg", "image/jpeg", 1)
            db.commit()

        await listing_generation.in_worker_session(add_photo)
        return None  # The fill rejected the stale evidence.

    monkeypatch.setattr("anteroom.services.native_field_fill.fill_native_fields", fill_native_fields)
    monkeypatch.setattr("anteroom.services.native_field_fill.fill_etsy_attributes", fill_etsy_attributes)
    monkeypatch.setattr("anteroom.services.marketplaces.get_selected_marketplaces", lambda: ["etsy"])
    monkeypatch.setattr(listing_generation.generation_timing, "add_stages",
                        lambda *args, **kwargs: outcomes.append(kwargs["outcome"]))
    try:
        with worker_sessions(engine):
            asyncio.run(listing_generation._finish_generation_background(
                conv_id, listing, evidence="short sleeves", provider=object(),
                run_started=0.0, expected_revision_id=revision_id, expected_photo_fingerprint=fingerprint))
        with Session(engine) as db:
            repo = ConversationRepo(db)
            assert repo.current_revision_id(conv_id) == revision_id
            assert len(repo.get_photos(conv_id)) == 1
            assert repo.get_messages(conv_id) == []
        assert outcomes == ["superseded"]
    finally:
        engine.dispose()
