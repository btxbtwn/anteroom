from __future__ import annotations

from datetime import UTC, datetime
import uuid

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vendoo_studio.database import Base, load_models, make_engine
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.ownership import LOCAL_USER_ID, SellerOwned
from vendoo_studio.services import backups, schema_migrations

PREVIOUS_REVISION = "3ec056c582ed"
OWNERSHIP_REVISION = "d8b6f2a19c40"
SHARED_TABLES = {"category_trees", "category_tree_nodes"}


@pytest.fixture
def migration_engine(tmp_path, monkeypatch):
    """Use the suite's database dialect, but never migrate its shared database."""
    from vendoo_studio.database import engine as application_engine

    monkeypatch.setattr(backups, "BACKUPS_DIR", str(tmp_path / "backups"))
    if application_engine.dialect.name == "sqlite":
        engine = make_engine(f"sqlite:///{tmp_path / 'ownership.db'}")
        yield engine
        engine.dispose()
        return

    name = "ownership_migration_" + uuid.uuid4().hex
    admin = application_engine.execution_options(isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(sa.text(f'CREATE DATABASE "{name}"'))
    engine = make_engine(application_engine.url.set(database=name).render_as_string(hide_password=False))
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(sa.text(f'DROP DATABASE "{name}" WITH (FORCE)'))


def migration_config(engine):
    return schema_migrations.alembic_config(engine.url.render_as_string(hide_password=False))


def seed_previous_release(engine):
    now = datetime.now(UTC)
    rows = {
        "conversations": {"id": "kept-item", "title": "Vintage coat", "notes": "Keep my notes"},
        "messages": {"id": "kept-message", "conversation_id": "kept-item", "role": "user", "text": "Keep the measurements",
                     "photo_fingerprint": "a" * 64},
        "photos": {"id": "kept-photo", "conversation_id": "kept-item", "original_filename": "coat.jpg",
                   "stored_filename": "kept-photo.jpg", "mime_type": "image/jpeg", "size_bytes": 1024, "display_order": 0},
        "listing_revisions": {"id": "kept-revision", "conversation_id": "kept-item",
                              "listing_json": {"title": "Vintage coat", "price": 42}, "source": "seller"},
        "listings": {"conversation_id": "kept-item", "current_revision_id": "kept-revision"},
        "jobs": {"id": "kept-job", "conversation_id": "kept-item", "approved_revision_id": "kept-revision",
                 "listing_snapshot": {"title": "Vintage coat"}, "status": "completed"},
        "job_events": {"id": "kept-event", "job_id": "kept-job", "sequence": 0, "event_type": "saved_draft"},
        "marketplace_sales": {"id": "kept-sale", "marketplace": "ebay", "order_id": "order-1", "line_id": "line-1",
                              "conversation_id": "kept-item", "state": "new", "received_at": now},
        "assistant_threads": {"id": "kept-thread", "title": "My shop", "created_at": now, "updated_at": now},
        "assistant_messages": {"id": "kept-assistant-message", "thread_id": "kept-thread", "role": "user",
                               "text": "Keep my shop history", "created_at": now},
    }
    metadata = sa.MetaData()
    with engine.begin() as connection:
        for name, values in rows.items():
            table = sa.Table(name, metadata, autoload_with=connection)
            connection.execute(table.insert().values(**values))
    return rows


def read_rows(engine, names):
    with engine.connect() as connection:
        metadata = sa.MetaData()
        return {
            name: [dict(row) for row in connection.execute(sa.select(sa.Table(name, metadata, autoload_with=connection))).mappings()]
            for name in names
        }


def test_every_model_has_explicit_ownership_or_is_public_taxonomy():
    load_models()
    shared = set()
    for mapper in Base.registry.mappers:
        table = mapper.local_table
        if not issubclass(mapper.class_, SellerOwned):
            shared.add(table.name)
            continue
        assert not table.c.user_id.nullable
        assert table.c.user_id.index
    assert shared == SHARED_TABLES


def test_previous_release_upgrade_preserves_records_and_constraints(migration_engine):
    engine = migration_engine
    command.upgrade(migration_config(engine), PREVIOUS_REVISION)
    names = seed_previous_release(engine)
    before = read_rows(engine, names)
    inspector = sa.inspect(engine)
    foreign_keys = {name: inspector.get_foreign_keys(name) for name in inspector.get_table_names()}
    old_indexes = {name: {index["name"] for index in inspector.get_indexes(name)} for name in inspector.get_table_names()}

    assert schema_migrations.ensure_schema(engine) == OWNERSHIP_REVISION
    assert schema_migrations.recorded_revision(engine) == OWNERSHIP_REVISION
    after = read_rows(engine, names)
    for name, rows in before.items():
        assert after[name] == [dict(row, user_id=LOCAL_USER_ID) for row in rows]

    inspector = sa.inspect(engine)
    for table in Base.metadata.tables.values():
        columns = {column["name"]: column for column in inspector.get_columns(table.name)}
        indexes = {index["name"] for index in inspector.get_indexes(table.name)}
        assert old_indexes[table.name] <= indexes
        assert inspector.get_foreign_keys(table.name) == foreign_keys[table.name]
        if table.name in SHARED_TABLES:
            assert "user_id" not in columns
        else:
            assert not columns["user_id"]["nullable"]
            assert f"ix_{table.name}_user_id" in indexes
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    # Reopening a migrated install must leave its records and ownership alone.
    assert schema_migrations.ensure_schema(engine) == OWNERSHIP_REVISION
    assert read_rows(engine, names) == after


def test_fresh_database_supports_local_and_explicit_seller_ownership(migration_engine):
    engine = migration_engine
    schema_migrations.ensure_schema(engine)
    seller = str(uuid.uuid4())
    with Session(engine) as db:
        db.add_all([Conversation(id="local", title="Local draft"),
                    Conversation(id="account", title="Account draft", user_id=seller)])
        db.commit()
        assert db.get(Conversation, "local").user_id == LOCAL_USER_ID
        assert db.get(Conversation, "account").user_id == seller
    # Existing SQL writers also keep working; an explicit NULL never does.
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO conversations (id, title) VALUES ('raw', 'SQL draft')"))
        assert connection.execute(sa.text("SELECT user_id FROM conversations WHERE id = 'raw'")).scalar_one() == LOCAL_USER_ID
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO conversations (id, user_id) VALUES ('unowned', NULL)"))


@pytest.mark.sqlite_only("only Mac SQLite databases predate Alembic")
def test_pre_alembic_database_gets_ownership_without_losing_rows(migration_engine):
    engine = migration_engine
    command.upgrade(migration_config(engine), PREVIOUS_REVISION)
    names = seed_previous_release(engine)
    before = read_rows(engine, names)
    with engine.begin() as connection:
        connection.execute(sa.text("DROP TABLE alembic_version"))
        # Adoption must handle a mix of old and newly created tables.
        connection.execute(sa.text("DROP TABLE similar_item_reviews"))
    assert schema_migrations.ensure_schema(engine) == OWNERSHIP_REVISION
    for name, rows in before.items():
        assert read_rows(engine, [name])[name] == [dict(row, user_id=LOCAL_USER_ID) for row in rows]
    assert schema_migrations.recorded_revision(engine) == OWNERSHIP_REVISION
    assert len(backups.list_snapshots(backups.BACKUPS_DIR)) == 1


@pytest.mark.sqlite_only("snapshots are written for local SQLite upgrades")
def test_upgrade_snapshot_keeps_the_previous_schema(migration_engine, tmp_path):
    engine = migration_engine
    command.upgrade(migration_config(engine), PREVIOUS_REVISION)
    names = seed_previous_release(engine)
    before = read_rows(engine, names)
    schema_migrations.ensure_schema(engine)
    snapshot = backups.list_snapshots(backups.BACKUPS_DIR)[0]
    restored = backups.decompress_snapshot(snapshot.path, tmp_path / "restored.db")
    snapshot_engine = make_engine(f"sqlite:///{restored}")
    try:
        assert read_rows(snapshot_engine, names) == before
        assert schema_migrations.recorded_revision(snapshot_engine) == PREVIOUS_REVISION
    finally:
        snapshot_engine.dispose()
