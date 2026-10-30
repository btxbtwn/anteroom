from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, get_db, load_models
from vendoo_studio.main import app
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.listing import Listing, ListingRevision
from vendoo_studio.repositories.queries import ListingRepo


def test_editor_reads_latest_body_and_keeps_full_revision_count():
    load_models()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Conversation(id="history"))
        now = datetime.now(UTC) - timedelta(seconds=40)
        for index in range(40):
            db.add(ListingRevision(
                id=f"revision-{index}", conversation_id="history", source="test",
                created_at=now + timedelta(seconds=index),
                listing_json={"title": f"Saved title {index}", "description": "Saved details " * 1000},
            ))
        db.add(Listing(conversation_id="history", current_revision_id="revision-39"))
        db.commit()
        db.expunge_all()
        loaded = []

        def on_load(session, instance):
            if isinstance(instance, ListingRevision):
                loaded.append(instance.id)

        event.listen(db, "loaded_as_persistent", on_load)

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        try:
            client = TestClient(app)
            response = client.get("/api/conversations/history/listing")
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["listing"]["title"] == "Saved title 39"
            assert set(loaded) == {"revision-39"}
            assert body["revision_count"] == db.query(func.count(ListingRevision.id)).scalar()

            loaded.clear()
            current_id = body["current_revision_id"]
            db.expunge_all()
            validated = client.post("/api/conversations/history/listing/validate")
            assert validated.status_code == 200, validated.text
            assert set(loaded) == {current_id}

            # History still exposes every saved revision in newest-first order.
            history = client.get("/api/conversations/history/revisions")
            assert history.status_code == 200
            assert len(history.json()) == body["revision_count"]
            assert history.json()[0]["title"] == "Saved title 39"
        finally:
            app.dependency_overrides.pop(get_db)
    engine.dispose()


def test_prefetching_a_listing_never_writes_even_when_old_values_need_normalizing():
    load_models()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Conversation(id="prefetch"))
        original = {"title": "Saved item", "price": 30, "mercari_specifics": {"price": 99}}
        db.add(ListingRevision(id="old", conversation_id="prefetch", source="test", listing_json=original))
        db.add(Listing(conversation_id="prefetch", current_revision_id="old"))
        db.commit()
        writes = []

        def record_write(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
                writes.append(statement)

        event.listen(engine, "before_cursor_execute", record_write)
        app.dependency_overrides[get_db] = lambda: db
        try:
            client = TestClient(app)
            for _ in range(2):
                response = client.get("/api/conversations/prefetch/listing")
                assert response.status_code == 200, response.text
                assert response.json()["current_revision_id"] == "old"
                assert response.json()["revision_count"] == 1
                assert response.json()["listing"] == original
            assert writes == []
            assert ListingRepo(db).get_revision("old").listing_json == original
            # Saving returns the canonical body, so the editor needs no follow-up GET.
            saved_response = client.put("/api/conversations/prefetch/listing", json={
                "listing": {**original, "price": 35}, "expected_revision_id": "old",
            })
            assert saved_response.status_code == 200, saved_response.text
            saved = saved_response.json()
            assert saved["listing"]["price"] == 35
            assert saved["listing"]["condition"] == "Pre-Owned - Good"
            assert saved["listing"] == ListingRepo(db).get_revision(saved["revision_id"]).listing_json
            assert client.get("/api/conversations/prefetch/listing").json()["current_revision_id"] == saved["revision_id"]
        finally:
            app.dependency_overrides.pop(get_db)
            event.remove(engine, "before_cursor_execute", record_write)

    engine.dispose()
