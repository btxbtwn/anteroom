import sqlite3

import pytest

from vendoo_studio.services.schema_drift import describe, inspect_database


@pytest.fixture
def database(tmp_path, monkeypatch):
    """A database built from the current models, at a throwaway path."""
    path = tmp_path / "studio.db"
    monkeypatch.setenv("VENDOO_STUDIO_DATA_DIR", str(tmp_path))
    from sqlalchemy import create_engine

    from vendoo_studio.database import Base, load_models

    load_models()
    Base.metadata.create_all(bind=create_engine(f"sqlite:///{path}"))
    return path


def test_current_schema_is_clean(database):
    drift = inspect_database(database)
    assert drift.clean
    assert "matches the models" in describe(drift)


def test_missing_table_is_reported_and_heals(database):
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE fill_log_entries")

    drift = inspect_database(database)
    assert "fill_log_entries" in drift.missing_tables
    assert not drift.clean
    assert drift.healed_by_init_db
    assert "No action needed" in describe(drift)


def test_missing_column_is_reported_as_unhealed(database):
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE conversations DROP COLUMN notes")

    drift = inspect_database(database)
    assert "conversations.notes" in drift.missing_columns
    assert not drift.healed_by_init_db
    assert "will NOT add these columns" in describe(drift)
    assert "BACKFILLED_COLUMNS" not in describe(drift)


def test_backfilled_column_is_reported_as_healing(database):
    """settled_at is named in BACKFILLED_COLUMNS, so startup restores it."""
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE conversations DROP COLUMN settled_at")

    drift = inspect_database(database)
    assert "conversations.settled_at" in drift.missing_columns
    assert drift.healed_by_init_db
    assert "No action needed" in describe(drift)


def test_unknown_table_is_listed_but_not_a_failure(database):
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE leftovers (id TEXT PRIMARY KEY)")

    drift = inspect_database(database)
    assert drift.clean
    assert "leftovers" in drift.unknown_tables
    assert "leftovers" in describe(drift)


def test_missing_database_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        inspect_database(tmp_path / "absent.db")


def _migrated_database(tmp_path, monkeypatch, revision):
    """A database the way a release at ``revision`` left it."""
    from alembic import command

    from vendoo_studio.services.schema_migrations import alembic_config

    monkeypatch.setenv("VENDOO_STUDIO_DATA_DIR", str(tmp_path))
    path = tmp_path / "studio.db"
    command.upgrade(alembic_config(f"sqlite:///{path}"), revision)
    return path


def _previous_revision():
    from alembic.script import ScriptDirectory

    from vendoo_studio.services.schema_migrations import alembic_config, head_revision

    previous = ScriptDirectory.from_config(alembic_config()).get_revision(head_revision()).down_revision
    assert isinstance(previous, str)
    return previous


def test_previous_release_database_is_healed_by_pending_migrations(tmp_path, monkeypatch):
    """Startup runs the revisions a database has not applied yet, so their columns heal."""
    from vendoo_studio.services.schema_migrations import head_revision

    previous = _previous_revision()
    path = _migrated_database(tmp_path, monkeypatch, previous)

    drift = inspect_database(path)
    assert not drift.clean
    assert drift.pending_revisions == (head_revision(),)
    assert drift.healed_by_init_db
    report = describe(drift)
    assert f"{previous} to {head_revision()}" in report
    assert "No action needed" in report
    assert "BACKFILLED_COLUMNS" not in report
    with sqlite3.connect(path) as connection:
        # Inspecting must not migrate the database it was pointed at.
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (previous,)


def test_drift_at_head_asks_for_a_revision(tmp_path, monkeypatch):
    path = _migrated_database(tmp_path, monkeypatch, "head")
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE conversations DROP COLUMN notes")

    drift = inspect_database(path)
    assert drift.pending_revisions == ()
    assert not drift.healed_by_init_db
    report = describe(drift)
    assert "will NOT add these columns: conversations.notes" in report
    assert "alembic revision" in report
    assert "BACKFILLED_COLUMNS" not in report


def test_database_from_a_newer_build_is_not_healed(tmp_path, monkeypatch):
    path = _migrated_database(tmp_path, monkeypatch, "head")
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE alembic_version SET version_num = 'f00dcafe9999'")

    drift = inspect_database(path)
    assert not drift.healed_by_init_db
    assert "newer version" in describe(drift)
