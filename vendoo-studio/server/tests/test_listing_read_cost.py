from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, get_db, load_models
from vendoo_studio.main import app
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.listing import Listing, ListingRevision


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
