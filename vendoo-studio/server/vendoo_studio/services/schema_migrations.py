"""Bring the database to the schema this build expects, or refuse to run.

Three situations, and they need different handling:

* **A fresh install.** No file, or a file with no tables. Run the migrations
  from nothing and record where they landed.
* **A database from before migrations existed.** It has tables but no
  ``alembic_version``. It is brought up to the current schema the way earlier
  builds did it, checked for leftover gaps, and only then recorded as current.
  Stamping it without that check would tell Alembic a lie it can never detect.
* **A database this build is behind.** Its recorded revision is one this build
  has never heard of, which means a newer Studio wrote it. Opening it could
  write rows the newer build cannot read, so this refuses instead.

Every path that changes the schema takes a snapshot first.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import Engine, inspect

import vendoo_studio
from vendoo_studio.config import DATABASE_PATH

log = logging.getLogger("vendoo_studio.schema")


class SchemaError(RuntimeError):
    """The database cannot be brought to the schema this build expects."""


def migrations_dir() -> Path:
    """Where the revision scripts live, in a checkout or inside the app bundle."""
    return Path(vendoo_studio.__file__).resolve().parent / "migrations"


def alembic_config(url: str | None = None) -> Config:
    """Alembic settings for ``url``, or the app's own database.

    The URL rides in ``attributes`` rather than the ini options: ini values are
    %-interpolated, and a Postgres URL carries percent-encoded characters and a
    password that must not be masked.
    """
    from vendoo_studio.database import database_url

    config = Config()
    config.set_main_option("script_location", str(migrations_dir()))
    config.attributes["url"] = url or database_url()
    return config


def head_revision() -> str:
    script = ScriptDirectory.from_config(alembic_config())
    head = script.get_current_head()
    if head is None:
        raise SchemaError(f"No migrations found in {migrations_dir()}")
    return head


def recorded_revision(engine: Engine) -> str | None:
    """What the database says it is, or None when it has never been stamped."""
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def _has_tables(engine: Engine) -> bool:
    return bool([name for name in inspect(engine).get_table_names() if name != "alembic_version"])


def _known(revision: str) -> bool:
    script = ScriptDirectory.from_config(alembic_config())
    try:
        script.get_revision(revision)
    except CommandError:
        return False
    return True


def database_path(engine: Engine) -> Path:
    """The file behind a SQLite engine; only meaningful for SQLite."""
    return Path(engine.url.database or DATABASE_PATH)


def _snapshot(reason: str, engine: Engine) -> None:
    from vendoo_studio.services.backups import BackupError, take_snapshot

    if engine.dialect.name != "sqlite":
        return  # The host's point-in-time backups cover a hosted Postgres.

    path = database_path(engine)
    if path.exists():
        try:
            take_snapshot(reason, source=path)
        except (BackupError, OSError) as exc:
            raise SchemaError(f"Database unchanged: a recovery snapshot could not be created. {exc}") from exc


def _adopt_legacy(engine: Engine, config: Config) -> str:
    """Record a pre-migrations database as current, once it really is current.

    Earlier builds carried a database forward with ``create_all`` plus a short
    list of added columns. That is done here as well, because a database from
    that era may still be short of both, and then verified: a gap left at this
    point becomes invisible the moment the stamp is written.
    """
    from vendoo_studio.database import Base, _ensure_sqlite_columns, load_models
    from vendoo_studio.services.schema_drift import describe, inspect_database

    if engine.dialect.name != "sqlite":
        # Only Mac databases predate migrations; a Postgres one was always migrated.
        raise SchemaError("This Postgres database has tables but no migration record.")
    log.info("Adopting a database from before migrations were introduced")
    _snapshot("pre-adopt", engine)

    load_models()
    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_columns(engine)

    # A pre-Alembic table cannot gain ownership through create_all. Run the
    # same additive revision used by versioned databases before verifying and
    # stamping it. Newly created tables already have ownership; the revision
    # handles that mixed schema without recreating tables or losing rows.
    scripts = ScriptDirectory.from_config(config)
    ownership = scripts.get_revision("d8b6f2a19c40")
    with engine.connect() as connection:
        # SQLite's documented table-rebuild procedure disables FK enforcement
        # before the transaction. Otherwise rebuilding a parent can fail or
        # cascade-delete its children. Verify all references before committing,
        # and always restore enforcement before returning this pooled connection.
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        try:
            with connection.begin(), Operations.context(MigrationContext.configure(connection)):
                ownership.module.upgrade()
                for revision in reversed(list(scripts.iterate_revisions("head", ownership.revision))):
                    revision.module.upgrade()
                if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
                    raise SchemaError("Database adoption found invalid references; restore the recovery snapshot.")
        finally:
            connection.rollback()
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()

    drift = inspect_database(database_path(engine))
    if not drift.clean:
        raise SchemaError(
            "This database cannot be brought to the current schema "
            "automatically:\n" + describe(drift)
        )

    head = head_revision()
    command.stamp(config, head)
    return head


def ensure_schema(engine: Engine | None = None) -> str:
    """Migrate the database to head and return the revision it now holds."""
    from vendoo_studio.database import engine as default_engine

    target = engine or default_engine
    config = alembic_config(target.url.render_as_string(hide_password=False))
    config.attributes["connection"] = None
    head = head_revision()

    current = recorded_revision(target)

    if current is None:
        if _has_tables(target):
            return _adopt_legacy(target, config)
        log.info("Creating a new database at revision %s", head)
        command.upgrade(config, "head")
        return head

    if not _known(current):
        raise SchemaError(
            f"This database is at revision {current}, which this build of Anteroom "
            "does not know. It was written by a newer version. Update Anteroom "
            "rather than opening it with this one, so the data is not written "
            "in a shape the newer version cannot read."
        )

    if current == head:
        return head

    log.info("Migrating the database from %s to %s", current, head)
    _snapshot("pre-migration", target)
    command.upgrade(config, "head")
    return head
