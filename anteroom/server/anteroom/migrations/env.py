"""Alembic environment for Studio's database: SQLite on the Mac, Postgres hosted.

Lives inside the package rather than beside it so the migration scripts travel
with the frozen Mac app: ``script_location`` is resolved from
``anteroom.__file__``, which points into the bundle when packaged and into
the checkout otherwise.
"""

from __future__ import annotations

import os
import sys
import time

from alembic import context
from sqlalchemy import Connection, Engine, create_engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from anteroom.database import Base, database_url, load_models

load_models()
target_metadata = Base.metadata


def _url() -> str:
    return (
        context.config.attributes.get("url")
        or context.config.get_main_option("sqlalchemy.url")
        or database_url()
    )


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        # SQLite cannot ALTER much in place. Batch mode copies the table,
        # which is what lets a migration rename or drop a column at all.
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# A revision that waits for a table lock queues every later query on that
# table behind it, so a busy production table would stall the app. Give up the
# lock request quickly; a revision that needs longer sets its own. The test
# suite turns it off ("0"): its workers migrate fresh databases at once and
# queue on the cluster-wide seller role.
LOCK_TIMEOUT_SETTING = "ANTEROOM_MIGRATION_LOCK_TIMEOUT"


def _limit_lock_waits(connection: Connection) -> None:
    if connection.dialect.name == "postgresql":
        timeout = os.environ.get(LOCK_TIMEOUT_SETTING) or "5s"
        connection.execute(text("SELECT set_config('lock_timeout', :timeout, false)"), {"timeout": timeout})
        # A session setting outlives this commit; Alembic then opens its own transaction.
        connection.commit()


def _migrate(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _migrate_on_engine(engine: Engine) -> None:
    retry_locks = (
        engine.dialect.name == "postgresql"
        and context.get_x_argument(as_dictionary=True).get("retry_lock_timeouts") == "1"
    )
    delays = (10, 20, 40) if retry_locks else ()
    committed = False

    def mark_commit(_connection: Connection) -> None:
        nonlocal committed
        # Alembic commits before each autocommit block. The attempt may have
        # effects that a rollback cannot undo once this boundary is crossed.
        committed = True

    for attempt, delay in enumerate((*delays, None), start=1):
        with engine.connect() as connection:
            _limit_lock_waits(connection)
            committed = False
            if delay is not None:
                event.listen(connection, "commit", mark_commit)
            try:
                _migrate(connection)
            except DBAPIError as error:
                if (
                    delay is None
                    or committed
                    or error.connection_invalidated
                    or getattr(error.orig, "sqlstate", None) != "55P03"
                ):
                    raise
            else:
                return

        print(
            f"Postgres migration lock unavailable (attempt {attempt}/{len(delays) + 1}). "
            f"Retrying in {delay}s.",
            file=sys.stderr,
            flush=True,
        )
        time.sleep(delay)


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _migrate(connection)
        return

    # Close the database session during retry backoff, including when the URL
    # uses the hosted session pooler.
    engine = create_engine(_url(), poolclass=NullPool)
    try:
        _migrate_on_engine(engine)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
