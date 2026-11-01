"""The editor never replaces a revision it has not read."""
from copy import deepcopy
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from vendoo_studio.services import listing_persistence
from vendoo_studio.database import Base, database_url, get_db, load_models, make_engine
from vendoo_studio.main import app
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo


@pytest.fixture
def editor():
    load_models()
    engine = (make_engine(database_url()) if os.environ.get("ANTEROOM_TEST_DATABASE_URL") else
              create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}))
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        conv = ConversationRepo(db).create("Coat")
        app.dependency_overrides[get_db] = lambda: db
        with TestClient(app) as client:
            yield client, db, conv.id
        app.dependency_overrides.pop(get_db, None)
        from vendoo_studio.services.listing_delete import delete_listing

        db.rollback()
        delete_listing(db, conv.id)
    engine.dispose()


def test_a_stale_editor_cannot_undo_a_new_description(editor):
    client, db, conv_id = editor
    original = {"title": "Coat", "description": "Original", "price": 40, "quantity": 1}
    revision = listing_persistence.save_revision(db, conv_id, original, "user_form")
    url = f"/api/conversations/{conv_id}/listing"
    first = client.put(url, json={"listing": {**original, "description": "Seller correction"},
                                  "expected_revision_id": revision.id})
    assert first.status_code == 200, first.text
    second = client.put(url, json={"listing": {**original, "price": 50}, "expected_revision_id": revision.id})
    assert second.status_code == 409
    current = ListingRepo(db).get_revision(first.json()["revision_id"])
    assert current.listing_json["description"] == "Seller correction"
    assert current.listing_json["price"] == 40
    assert len(ListingRepo(db).get_revisions(conv_id)) == 2


def test_an_editor_must_name_the_revision_it_read(editor):
    client, db, conv_id = editor
    revision = listing_persistence.save_revision(db, conv_id, {"title": "Coat", "price": 40}, "user_form")
    reply = client.put(f"/api/conversations/{conv_id}/listing", json={"listing": {"title": "Stale"}})
    assert reply.status_code == 422
    assert ListingRepo(db).get_current(conv_id).current_revision_id == revision.id


def test_a_first_edit_cannot_replace_a_revision_created_elsewhere(editor):
    client, db, conv_id = editor
    body = {"listing": {"title": "First edit", "price": 40}, "expected_revision_id": ""}
    first = client.put(f"/api/conversations/{conv_id}/listing", json=deepcopy(body))
    assert first.status_code == 200, first.text
    body["listing"]["title"] = "Another first edit"
    assert client.put(f"/api/conversations/{conv_id}/listing", json=body).status_code == 409
    assert ListingRepo(db).get_current(conv_id).current_revision_id == first.json()["revision_id"]


def test_a_placeholder_listing_accepts_its_first_revision_once(editor):
    from vendoo_studio.models.listing import Listing

    client, db, conv_id = editor
    db.add(Listing(conversation_id=conv_id))
    db.commit()
    body = {"listing": {"title": "First edit", "price": 40}, "expected_revision_id": ""}
    assert client.put(f"/api/conversations/{conv_id}/listing", json=body).status_code == 200
    assert client.put(f"/api/conversations/{conv_id}/listing", json=body).status_code == 409


def test_a_field_command_does_not_overwrite_a_concurrent_edit(editor, monkeypatch):
    client, db, conv_id = editor
    listings = ListingRepo(db)
    listing_persistence.save_revision(listings.db, conv_id, {"title": "Original", "price": 40}, "user_form")

    def concurrent_edit(listing, updates):
        listing_persistence.save_revision(listings.db, conv_id, {"title": "Newer seller edit", "price": 50}, "user_form")
        listing["price"] = 99

    monkeypatch.setattr("vendoo_studio.services.seller_overrides.apply_field_updates", concurrent_edit)
    reply = client.post(f"/api/conversations/{conv_id}/fields", json={"path": "price", "value": 99})
    assert reply.status_code == 409
    latest = listings.get_revision(listings.get_current(conv_id).current_revision_id)
    assert latest.listing_json == {"title": "Newer seller edit", "price": 50}
