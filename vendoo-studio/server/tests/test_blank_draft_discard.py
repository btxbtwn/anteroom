"""New listing leaves no draft behind when the seller adds nothing to it."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, get_db
from vendoo_studio.main import app
from vendoo_studio.models.conversation import Photo
from vendoo_studio.models.fill_log import FillLogEntry  # noqa: F401
from vendoo_studio.models.job import Job  # noqa: F401
from vendoo_studio.models.registry import FieldRegistry  # noqa: F401
from vendoo_studio.repositories.queries import ConversationRepo


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app), db
    finally:
        app.dependency_overrides.clear()
        db.close()


def _new(client) -> str:
    return client.post("/api/conversations", json={"title": "New Listing"}).json()["id"]


def test_an_untouched_new_listing_is_discarded(client):
    http, db = client
    identity = _new(http)
    response = http.post(f"/api/conversations/{identity}/discard-blank")
    assert response.status_code == 200, response.text
    assert response.json() == {"discarded": True}
    db.expire_all()
    assert ConversationRepo(db).get(identity) is None
    assert http.get(f"/api/conversations/{identity}").status_code == 404


def test_a_listing_with_typed_details_is_kept(client):
    http, _ = client
    identity = _new(http)
    assert http.patch(f"/api/conversations/{identity}", json={"notes": "Small stain on cuff"}).status_code == 200
    assert http.post(f"/api/conversations/{identity}/discard-blank").json() == {"discarded": False}
    assert http.get(f"/api/conversations/{identity}").status_code == 200


def test_a_listing_with_saved_fields_is_kept(client):
    http, _ = client
    identity = _new(http)
    saved = http.put(f"/api/conversations/{identity}/listing", json={"listing": {"title": "Wool coat"}, "expected_revision_id": ""})
    assert saved.status_code == 200, saved.text
    assert http.post(f"/api/conversations/{identity}/discard-blank").json() == {"discarded": False}
    assert http.get(f"/api/conversations/{identity}").status_code == 200


def test_a_listing_with_a_photo_is_kept(client):
    http, db = client
    identity = _new(http)
    db.add(Photo(conversation_id=identity, original_filename="a.jpg", stored_filename="a.jpg", mime_type="image/jpeg", size_bytes=1))
    db.commit()
    assert http.post(f"/api/conversations/{identity}/discard-blank").json() == {"discarded": False}
    assert http.get(f"/api/conversations/{identity}").status_code == 200


def test_a_renamed_listing_is_kept(client):
    http, _ = client
    identity = _new(http)
    assert http.patch(f"/api/conversations/{identity}", json={"title": "Coat"}).status_code == 200
    assert http.post(f"/api/conversations/{identity}/discard-blank").json() == {"discarded": False}


def test_a_missing_listing_is_not_found(client):
    http, _ = client
    assert http.post("/api/conversations/nope/discard-blank").status_code == 404
