"""Item Details saves send only the keys the seller changed, merged into the notes as they are now.

A save used to read the notes, merge on the client and write the whole string
back, so a key the server wrote in between (a category override, a delist
record) was put back to what the client had read.
"""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from test_hosted_drafts import cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import jwks, signing_keys  # noqa: F401
from vendoo_studio.database import Base, get_db
from vendoo_studio.main import app
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.repositories.queries import ConversationRepo
from vendoo_studio.services.vendoo_import import merge_note_changes, merge_notes


def test_merge_note_changes_sets_only_the_changed_keys():
    notes = json.dumps({"categoryOverride": "Shirts", "sku": "OLD", "measurements": {"top": {"pitToPit": "20", "length": "28"}}})
    merged = json.loads(merge_note_changes(notes, {"sku": "NEW", "measurements": {"top": {"length": "30"}, "pants": {"waist": "16"}}}))
    assert merged == {"categoryOverride": "Shirts", "sku": "NEW",
                      "measurements": {"top": {"pitToPit": "20", "length": "30"}, "pants": {"waist": "16"}}}


def test_merge_note_changes_keeps_plain_text_notes_as_seller_notes():
    assert json.loads(merge_note_changes("Small stain on cuff", {"sku": "A1"})) == {"sellerNotes": "Small stain on cuff", "sku": "A1"}
    assert json.loads(merge_note_changes(None, {"sku": "A1"})) == {"sku": "A1"}


@pytest.fixture
def desktop():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    make = sessionmaker(bind=engine)

    def db():
        session = make()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = db
    try:
        yield TestClient(app), make
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_desktop_detail_save_keeps_keys_the_server_wrote_meanwhile(desktop):
    client, make = desktop
    with make() as db:
        conv = ConversationRepo(db).create(title="Tee", notes=json.dumps({"sku": "A1", "categoryOverride": "Shirts"}))
        identity = conv.id
    # A category lookup writes after the editor read the notes.
    with make() as db:
        row = db.get(Conversation, identity)
        row.notes = merge_notes(row.notes, {"categoryOverride": "Tops"})
        db.commit()
    saved = client.patch(f"/api/conversations/{identity}", json={"note_changes": {"knownFlaws": "Pilling"}})
    assert saved.status_code == 200, saved.text
    assert json.loads(saved.json()["notes"]) == {"sku": "A1", "categoryOverride": "Tops", "knownFlaws": "Pilling"}


def test_desktop_rename_with_note_changes_keeps_the_new_title(desktop):
    client, make = desktop
    with make() as db:
        identity = ConversationRepo(db).create(title="Tee", notes=json.dumps({"sku": "A1"})).id
    saved = client.patch(f"/api/conversations/{identity}",
                         json={"title": "Renamed", "note_changes": {"knownFlaws": "Pilling"}})
    assert saved.status_code == 200, saved.text
    with make() as db:
        row = db.get(Conversation, identity)
        assert row is not None
        assert row.title == "Renamed"
        assert json.loads(row.notes) == {"sku": "A1", "knownFlaws": "Pilling", "titlePinned": True}


def test_desktop_notes_with_note_changes_keeps_both(desktop):
    client, make = desktop
    with make() as db:
        identity = ConversationRepo(db).create(title="Tee", notes=json.dumps({"sku": "A1"})).id
    saved = client.patch(f"/api/conversations/{identity}",
                         json={"notes": json.dumps({"sellerNotes": "Smoke free"}), "note_changes": {"knownFlaws": "Pilling"}})
    assert saved.status_code == 200, saved.text
    assert json.loads(saved.json()["notes"]) == {"sku": "A1", "sellerNotes": "Smoke free", "knownFlaws": "Pilling"}


def test_hosted_detail_save_keeps_keys_the_server_wrote_meanwhile(cloud, signing_keys):  # noqa: F811
    client, engine = cloud
    alice = headers(signing_keys)
    notes = json.dumps({"sku": "A1", "categoryOverride": "Shirts", "measurements": {"top": {"pitToPit": "20"}}})
    identity = client.post("/api/drafts", headers=alice, json={"title": "Tee", "notes": notes}).json()["id"]
    with Session(engine) as db:
        row = db.get(Conversation, identity)
        assert row is not None
        row.notes = merge_notes(row.notes, {"categoryOverride": "Tops"})
        db.commit()
    saved = client.patch(f"/api/conversations/{identity}", headers=alice,
                         json={"note_changes": {"knownFlaws": "Pilling", "measurements": {"top": {"length": "28"}}}})
    assert saved.status_code == 200, saved.text
    assert json.loads(saved.json()["notes"]) == {"sku": "A1", "categoryOverride": "Tops", "knownFlaws": "Pilling",
                                                 "measurements": {"top": {"pitToPit": "20", "length": "28"}}}
