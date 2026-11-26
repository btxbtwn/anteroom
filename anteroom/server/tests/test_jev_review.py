"""The optional Jev pass only corrects demonstrably wrong, model-owned draft choices."""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
import httpx
from sqlalchemy.orm import sessionmaker

from anteroom.database import Base
from anteroom.repositories.queries import ConversationRepo, ListingRepo, RevisionConflict
from anteroom.services import jev_review, listing_persistence, native_contract, user_settings
from anteroom.services import business_context, category_fields  # noqa: F401 - register commit listeners before the first write
from anteroom.services.listing_generate import notes_fingerprint, photo_fingerprint
from worker_sessions import memory_engine, worker_sessions


@pytest.fixture
def draft(tmp_path, monkeypatch):
    monkeypatch.setattr(user_settings, "settings_path", lambda: tmp_path / "settings.json")
    engine = memory_engine()
    Base.metadata.create_all(engine)
    with worker_sessions(engine), sessionmaker(bind=engine)() as db:
        conv = ConversationRepo(db).create("Test jacket")
        path = "ebay_specifics.Outer Shell Material"
        quote = "Shell: cotton. Lining: wool."
        row = {"path": path, "key": path, "label": "Outer Shell Material", "options": ["Cotton", "Wool"],
               "option_values": {"Cotton": "C", "Wool": "W"}, "selection_only": True}
        listing = {"ebay_specifics": {"Outer Shell Material": "W"}, "title": "Jacket", "price": 45}
        form = {"ebay": {"category_id": "coat", "fields": [row]}}
        listing[native_contract.KEY] = native_contract.capture(listing, form, form)
        listing[native_contract.EVIDENCE_KEY] = {path: {"value": "W", "quote": quote}}
        revision = listing_persistence.save_revision(db, conv.id, listing, source="native_fields")
        yield db, conv.id, revision, path, quote
    engine.dispose()


def test_corrects_only_with_both_decisions_and_records_what_changed(draft):
    db, identity, initial, path, quote = draft
    args = identity, quote, photo_fingerprint([]), initial.listing_json, notes_fingerprint(None)
    assert not user_settings.get_ui_prefs()["jev_field_review"]
    answers = {"field0": {"type": "choice", "choice": "Cotton", "probabilities": {"Cotton": 0.99}},
               "wrong0": {"type": "noul", "noul": 0.99}}
    ask = AsyncMock(return_value=answers)
    with patch.object(jev_review, "_decide", ask):
        asyncio.run(jev_review.review_generation(*args))
        ask.assert_not_called()
        user_settings.set_ui_prefs(jev_field_review=True)
        listing = asyncio.run(jev_review.review_generation(*args))
    assert listing["ebay_specifics"]["Outer Shell Material"] == "C"
    assert listing[native_contract.EVIDENCE_KEY][path]["quote"] == quote
    assert listing["price"] == 45
    db.expire_all()
    revision = ListingRepo(db).get_revisions(identity)[0]
    assert revision.parent_revision_id == initial.id and revision.source == "jev_review"
    assert any("Wool → Cotton" in m.text for m in ConversationRepo(db).get_messages(identity))


@pytest.mark.parametrize(("choice", "probability", "contradiction"), [
    ("Wool", 1, 1), ("Silk", 1, 1), ("Insufficient evidence", 1, 1), ("Cotton", 0.89, 1),
    ("Cotton", 1, 0.89), ("Cotton", True, True), ("Cotton", 1, None),
])
def test_abstains_without_a_supported_replacement(choice, probability, contradiction):
    field = jev_review.Field("ebay_specifics.Material", "Material", "Wool", {"Wool": "W", "Cotton": "C"}, "Cotton.")
    answers = {"field0": {"type": "choice", "choice": choice, "probabilities": {choice: probability}},
               "wrong0": {"type": "noul", "noul": contradiction}}
    assert not jev_review._changes([field], answers)


def test_skips_seller_owned_missing_and_unsubstantiated_fields(draft):
    _, _, initial, path, quote = draft
    listing = native_contract.copy_listing(initial.listing_json)
    assert not jev_review._fields(listing, "Similar items are wool.")
    for lock in [path.lower(), "ebay_specifics"]:
        assert not jev_review._fields({**listing, "seller_fields": [lock]}, quote)
    listing["ebay_specifics"]["Outer Shell Material"] = ""
    assert not jev_review._fields(listing, quote)


@pytest.mark.parametrize("action", ["disable", "edit", "notes"])
def test_seller_changes_during_review_take_precedence(draft, action):
    db, identity, initial, _, quote = draft
    args = identity, quote, photo_fingerprint([]), initial.listing_json, notes_fingerprint(None)
    user_settings.set_ui_prefs(jev_field_review=True)

    async def decide(*_):
        if action == "disable":
            user_settings.set_ui_prefs(jev_field_review=False)
        elif action == "notes":
            ConversationRepo(db).get(identity).notes = '{"sellerNotes":"Corrected label reading"}'
            db.commit()
        else:
            listing_persistence.save_revision(db, identity, {**initial.listing_json, "title": "My jacket"},
                                              source="user_form", expected_revision_id=initial.id)
        return {"field0": {"type": "choice", "choice": "Cotton", "probabilities": {"Cotton": 1}},
                "wrong0": {"type": "noul", "noul": 1}}

    with patch.object(jev_review, "_decide", decide):
        if action == "disable":
            assert asyncio.run(jev_review.review_generation(*args)) is None
        else:
            with pytest.raises(RevisionConflict):
                asyncio.run(jev_review.review_generation(*args))
    assert all(rev.source != "jev_review" for rev in ListingRepo(db).get_revisions(identity))


def test_provider_failure_preserves_the_saved_draft(draft):
    db, identity, initial, _, quote = draft
    user_settings.set_ui_prefs(jev_field_review=True)
    request = httpx.Request("POST", jev_review.ENDPOINT)
    failure = httpx.HTTPStatusError("Payment required", request=request, response=httpx.Response(402, request=request))
    with patch.object(jev_review, "_decide", AsyncMock(side_effect=failure)):
        assert asyncio.run(jev_review.review_generation(identity, quote, photo_fingerprint([]),
                           initial.listing_json, notes_fingerprint(None))) is None
    assert ListingRepo(db).latest_revision(identity).id == initial.id
    assert any("insufficient credit" in msg.text for msg in ConversationRepo(db).get_messages(identity))


def test_generation_finishes_with_review_of_the_current_models_answer(draft, monkeypatch):
    from anteroom.services import listing_generation

    db, identity, initial, path, quote = draft
    user_settings.set_ui_prefs(jev_field_review=True)
    blank = native_contract.copy_listing(initial.listing_json)
    blank.pop(native_contract.EVIDENCE_KEY)
    blank["ebay_specifics"].clear()
    revision = listing_persistence.save_revision(db, identity, blank, source="native_schema")
    model = AsyncMock(return_value=json.dumps({"fields": {path: {"value": "Wool", "evidence": quote}}}))
    monkeypatch.setattr("anteroom.services.listing_generate.collect_provider_text", model)
    monkeypatch.setattr("anteroom.services.marketplaces.get_selected_marketplaces", lambda: ["ebay"])
    monkeypatch.setattr(jev_review, "_key", lambda: ("test", False))
    requests = []

    def decisions(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json={"answers": {
            "field0": {"type": "choice", "choice": "Cotton", "probabilities": {"Cotton": 0.97}},
            "wrong0": {"type": "noul", "noul": 0.95},
        }})

    monkeypatch.setattr(jev_review, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(decisions)))
    asyncio.run(listing_generation._finish_generation_background(
        identity, revision.listing_json, evidence=f"{quote}\nSimilar sold coat was leather.", provider=object(),
        review_evidence=quote, review_notes=notes_fingerprint(None), expected_revision_id=revision.id,
        expected_photo_fingerprint=photo_fingerprint([]),
    ))
    db.expire_all()
    latest, generated = ListingRepo(db).get_revisions(identity)[:2]
    assert model.await_count == 1 and len(requests) == 1
    assert requests[0]["state"] == quote
    assert latest.source == "jev_review" and latest.parent_revision_id == generated.id
    assert generated.listing_json["ebay_specifics"]["Outer Shell Material"] == "W"
    assert latest.listing_json["ebay_specifics"]["Outer Shell Material"] == "C"
