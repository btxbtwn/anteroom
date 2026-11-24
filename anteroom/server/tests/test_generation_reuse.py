"""Retrying or regenerating a draft reruns only the steps whose inputs changed (#2037)."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import Session

from worker_sessions import memory_engine, worker_sessions

from anteroom.database import Base, load_models
from anteroom.providers.sandbox import EVIDENCE
from anteroom.repositories.queries import ConversationRepo, ListingRepo
from anteroom.routes import conversations
from anteroom.services import generation_timing, listing_generation, listing_persistence
from anteroom.services.listing_generate import photo_fingerprint, seller_item_details
from anteroom.services.streaming import GenerationRun

NOTES = json.dumps({"condition": "Good", "sellerNotes": "Soft cotton tee"})


class Reader:
    """A vision model that counts how often it is asked to read the photos."""

    def __init__(self) -> None:
        self.calls = 0

    async def analyze_photos(self, _paths, **_kwargs) -> dict:
        self.calls += 1
        return {"evidence": json.loads(json.dumps(EVIDENCE))}


@pytest.fixture
def draft():
    load_models()
    engine = memory_engine()
    Base.metadata.create_all(engine)
    with worker_sessions(engine), Session(engine) as db:
        conv = ConversationRepo(db).create(title="Tee", notes=NOTES)
        listing_persistence.add_photo(db, conv.id, "a.jpg", "a.jpg", "image/jpeg", 10, checksum="a")
        yield db, conv.id
    engine.dispose()


@pytest.fixture
def models():
    """Every model step after the photo read is faked; the listing writer fails, as after a provider error."""
    choose = AsyncMock(return_value={"general": "Men > Tops > T-Shirts"})
    with (
        patch.object(listing_generation, "load_skill_rules", return_value="rules"),
        patch.object(listing_generation, "comps_search_available", return_value=False),
        patch.object(listing_generation, "generation_is_current", return_value=True),
        patch.object(generation_timing, "record_run", return_value=None),
        patch("anteroom.services.category_selection.select_categories", choose),
        patch("anteroom.services.direct_categories.attach_direct_categories", AsyncMock(return_value={})),
        patch("anteroom.services.native_field_fill.prepare_generation_forms", AsyncMock(return_value={})),
        patch("anteroom.services.option_lookup.chat_with_options", side_effect=RuntimeError("listing model down")),
    ):
        yield choose


def generate(db: Session, conv_id: str, reader: Reader) -> list[str]:
    """One Generate press, as the route starts it: inputs read now, then the run."""
    db.expire_all()
    repo = ConversationRepo(db)
    notes = repo.get(conv_id).notes or ""
    run = GenerationRun()
    asyncio.run(listing_generation.run_listing_generation(
        run, conv_id=conv_id, provider=object(), vision_provider=reader,
        vision_name="test", vision_model="test", notes=notes, item_details=seller_item_details(notes),
        seller_answers="", paths=["a.jpg"], photo_count=len(repo.get_photos(conv_id)),
        analysis_fingerprint=photo_fingerprint(repo.get_photos(conv_id)),
    ))
    return run.history


def regenerate(db: Session, conv_id: str) -> None:
    """Regenerate's wipe, as the reset route runs it with keep_inputs."""
    db.expire_all()
    notes = conversations._carried_notes(db, conv_id, ConversationRepo(db).get(conv_id).notes)
    conversations._wipe(db, conv_id, True, notes)
    db.commit()


def test_retry_after_an_error_reuses_the_photo_reading_and_category(draft, models):
    db, conv_id = draft
    reader = Reader()
    assert any("listing model down" in event for event in generate(db, conv_id, reader))
    assert (reader.calls, models.await_count) == (1, 1)

    generate(db, conv_id, reader)
    # Same photos, same Item Details: neither the photos nor the category are asked again.
    assert (reader.calls, models.await_count) == (1, 1)
    db.expire_all()
    assert ListingRepo(db).get_revisions(conv_id)[0].listing_json["category_path"] == "Men > Tops > T-Shirts"


def test_regenerate_reuses_the_photo_reading(draft, models):
    db, conv_id = draft
    reader = Reader()
    generate(db, conv_id, reader)
    # The listing the wipe carries facts from, so Item Details change as they do for a seller.
    listing_persistence.save_revision(db, conv_id, {"title": "Levi's tee", "price": 24}, source="model")
    regenerate(db, conv_id)
    assert "askingPrice" in json.loads(ConversationRepo(db).get(conv_id).notes)

    generate(db, conv_id, reader)
    assert reader.calls == 1


@pytest.mark.parametrize("change", ["notes", "photos"])
def test_changed_notes_or_photos_are_read_again(draft, models, change):
    db, conv_id = draft
    reader = Reader()
    generate(db, conv_id, reader)
    if change == "notes":
        ConversationRepo(db).get(conv_id).notes = json.dumps({"condition": "Good", "sellerNotes": "Wool, not cotton"})
        db.commit()
    else:
        listing_persistence.add_photo(db, conv_id, "b.jpg", "b.jpg", "image/jpeg", 10, checksum="b")

    generate(db, conv_id, reader)
    assert (reader.calls, models.await_count) == (2, 2)


def test_regenerate_after_a_notes_edit_reads_the_photos_again(draft, models):
    db, conv_id = draft
    reader = Reader()
    generate(db, conv_id, reader)
    ConversationRepo(db).get(conv_id).notes = json.dumps({"condition": "Fair", "sellerNotes": "Soft cotton tee"})
    db.commit()
    regenerate(db, conv_id)

    generate(db, conv_id, reader)
    assert reader.calls == 2


def test_a_new_category_override_chooses_the_category_again(draft, models):
    db, conv_id = draft
    reader = Reader()
    generate(db, conv_id, reader)
    ConversationRepo(db).get(conv_id).notes = json.dumps({
        "condition": "Good", "sellerNotes": "Soft cotton tee", "categoryOverride": "Women > Tops"})
    db.commit()

    generate(db, conv_id, reader)
    assert models.await_count == 2
    assert models.await_args.args[-1] == "Women > Tops"
