"""Stored Vendoo label ids become Anteroom label names, once and for good."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from vendoo_studio.services import listing_persistence
from vendoo_studio.database import SessionLocal
from vendoo_studio.main import app
from vendoo_studio.models.listing import ListingRevision
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services import user_settings
from vendoo_studio.services import vendoo_label_catalog as catalog

WOMEN, A19, LOST = "g8MHWF7KiscANFLZRGyM", "2s8UVsWUw2lSYIzmFjsM", "oqkg04uSSvM58xHbp7DX"
NAMES = {WOMEN: "Women", A19: "A19"}


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def item(db):
    catalog.clear_for_tests()
    # Other tests' items are already converted, so counts below are this item's.
    catalog.convert_stored_labels(db, NAMES)
    conv = ConversationRepo(db).create(title="Imported coat")
    conv.notes = json.dumps({"vendooItemId": "item1", "vendooLabels": f"{WOMEN}, A19, {LOST}, dirtywhitesportstees"})
    repo = ListingRepo(db)
    listing_persistence.save_revision(repo.db, conv.id, {"title": "Imported coat", "labels": [A19]}, source="import")
    listing_persistence.save_revision(repo.db, conv.id, {"title": "Imported coat", "labels": [WOMEN, A19, "A19"]}, source="import")
    db.commit()
    yield conv
    user_settings.set_ui_prefs(label_colors={}, recent_vendoo_labels=[])


def _labels(db, conv_id):
    db.expire_all()
    notes = json.loads(ConversationRepo(db).get(conv_id).notes)["vendooLabels"]
    revisions = [row.listing_json["labels"] for row in db.query(ListingRevision).filter_by(conversation_id=conv_id).order_by(ListingRevision.created_at)]
    return notes, revisions


def test_ids_become_names_everywhere_and_unknown_ids_stay_visible(db, item):
    user_settings.set_ui_prefs(label_colors={WOMEN: "pink", A19: "blue", "a19": "teal"}, recent_vendoo_labels=[WOMEN, "Closet"])

    preview = catalog.convert_stored_labels(db, NAMES, dry_run=True)
    assert (preview["items"], preview["converted"]) == (1, 4) and LOST in preview["unknown"]
    assert _labels(db, item.id)[0].startswith(WOMEN)

    assert catalog.convert_stored_labels(db, NAMES) == preview
    notes, revisions = _labels(db, item.id)
    assert notes == f"Women, A19, {LOST}, dirtywhitesportstees"
    assert revisions == [["A19"], ["Women", "A19"]]
    prefs = user_settings.get_ui_prefs()
    assert prefs["label_colors"] == {"Women": "pink", "a19": "teal"}
    assert prefs["recent_vendoo_labels"] == ["Women", "Closet"]


def test_running_again_changes_nothing(db, item):
    catalog.convert_stored_labels(db, NAMES)
    before = _labels(db, item.id)
    again = catalog.convert_stored_labels(db, NAMES)
    assert (again["items"], again["converted"]) == (0, 0) and LOST in again["unknown"]
    assert _labels(db, item.id) == before


def test_a_later_name_for_an_unknown_id_converts_it(db, item):
    catalog.convert_stored_labels(db, NAMES)
    assert LOST not in catalog.convert_stored_labels(db, {**NAMES, LOST: "Raghouse"})["unknown"]
    assert _labels(db, item.id)[0] == "Women, A19, Raghouse, dirtywhitesportstees"


def test_the_seller_converts_from_settings_with_vendoo_names_when_chrome_answers(db, item):
    client = TestClient(app)
    with patch("vendoo_studio.services.vendoo_create.label_display_map", AsyncMock(return_value=NAMES)):
        pending = client.get("/api/imports/vendoo/label-names").json()
        done = client.post("/api/imports/vendoo/label-names").json()
        after = client.get("/api/imports/vendoo/label-names").json()
    # Before Vendoo is asked, ids the saved catalog has not seen are listed as unknown.
    assert pending["converted"] == 0 and {WOMEN, A19, LOST} <= set(pending["unknown"])
    assert done["converted"] >= 4 and LOST in done["unknown"]
    assert after["items"] == 0 and LOST in after["unknown"]


def test_item_details_show_what_is_stored(db, item):
    catalog.remember(NAMES)
    body = TestClient(app).get(f"/api/conversations/{item.id}").json()
    catalog.clear_for_tests()
    assert body["vendoo_labels"][0] == WOMEN


def test_a_twenty_letter_name_is_not_an_id():
    assert not catalog.looks_like_label_id("dirtywhitesportstees")
    assert not catalog.looks_like_label_id("DIRTYWHITESPORTSTEES")
    assert catalog.looks_like_label_id(WOMEN)
