from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from vendoo_studio.database import Base, get_db, load_models
from vendoo_studio.main import app
from vendoo_studio.models.conversation import Conversation, Photo
from vendoo_studio.repositories.queries import ConversationRepo


def test_cover_query_returns_only_first_photo_per_listing():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    load_models()
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([Conversation(id=key) for key in ("first", "second", "empty")])
        now = datetime.now(UTC)
        for conv_id in ("first", "second"):
            for index, order in enumerate((2, 0, 0, 1)):
                db.add(Photo(
                    id=f"{conv_id}-{index}", conversation_id=conv_id,
                    original_filename="photo.jpg", stored_filename="photo.jpg",
                    mime_type="image/jpeg", size_bytes=10, display_order=order,
                    created_at=now + timedelta(seconds=index), checksum=str(index),
                ))
        db.commit()
        statements = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        repo = ConversationRepo(db)
        covers = repo.cover_photos()
        assert len(statements) == 1
        # The database returns one row per listing rather than all eight photos.
        with engine.connect() as connection:
            assert len(connection.exec_driver_sql(statements[0], (1,)).all()) == 2
        assert covers == {key: (f"{key}-1", "1") for key in ("first", "second")}
        assert covers["first"] == repo.cover_photo("first")
        assert repo.cover_photo("empty") is None
        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        try:
            response = TestClient(app).get("/api/conversations")
            assert response.status_code == 200
            rows = {row["id"]: row for row in response.json()}
            assert len(rows) == 3
            assert "first-1" in rows["first"]["cover_photo_url"]
            assert rows["empty"]["cover_photo_url"] is None
        finally:
            app.dependency_overrides.pop(get_db)
    engine.dispose()
