"""Report where a SQLite file's schema differs from the models.

``init_db`` carries a database forward by running the Alembic revisions it has
not applied yet. A database from before migrations existed is adopted instead:
absent tables are created and the columns in ``BACKFILLED_COLUMNS`` added.
Nothing fails loudly when a gap is left behind: the mismatch surfaces later as
a query error. This reports the gap directly, and whether starting Anteroom
closes it, so a database that has sat through several updates can be checked
instead of assumed.
"""

from __future__ import annotations

import sqlite3
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from vendoo_studio.config import DATABASE_PATH
from vendoo_studio.database import BACKFILLED_COLUMNS, Base, load_models


@dataclass(frozen=True)
class Drift:
    """What the models expect that the database does not have, and vice versa."""

    missing_tables: tuple[str, ...]
    missing_columns: tuple[str, ...]
    unknown_tables: tuple[str, ...]
    # The recorded Alembic revision; None for a database from before migrations.
    revision: str | None = None
    # Revisions starting Anteroom applies, oldest first.
    pending_revisions: tuple[str, ...] = ()
    # Gaps left once those revisions have run. None when the database is not
    # migrated (never stamped), so only the legacy adoption rules apply.
    after_migrations: tuple[str, ...] | None = None
    # Recorded by a newer build: starting this one refuses the database.
    newer_revision: bool = False

    @property
    def unhealed(self) -> tuple[str, ...]:
        """Missing tables and columns that starting Anteroom will not add.

        A migrated database is healed only by its pending revisions. One from
        before migrations is adopted: ``create_all`` adds absent tables, and a
        missing column heals only when ``BACKFILLED_COLUMNS`` names it.
        """
        if self.after_migrations is not None:
            return self.after_migrations
        return tuple(
            name
            for name in self.missing_columns
            if name.split(".", 1)[1] not in BACKFILLED_COLUMNS.get(name.split(".", 1)[0], {})
        )

    @property
    def healed_by_init_db(self) -> bool:
        """True when a normal startup closes every gap found."""
        return not self.newer_revision and not self.unhealed

    @property
    def clean(self) -> bool:
        return not self.missing_tables and not self.missing_columns


def _database_schema(path: Path) -> dict[str, set[str]]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
            if not row[0].startswith("sqlite_")
        ]
        return {
            # table_info omits generated columns; table_xinfo includes them.
            table: {row[1] for row in connection.execute(f'PRAGMA table_xinfo("{table}")')}
            for table in tables
        }
    finally:
        connection.close()


def _recorded_revision(path: Path) -> str | None:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        connection.close()
    return row[0] if row else None


def _gaps(present: dict[str, set[str]]) -> tuple[list[str], list[str]]:
    expected = Base.metadata.tables
    missing_tables = [name for name in expected if name not in present]
    missing_columns = [
        f"{name}.{column.name}"
        for name, table in expected.items()
        if name in present
        for column in table.columns
        if column.name not in present[name]
    ]
    return missing_tables, missing_columns


def _gaps_after_migrating(path: Path) -> tuple[str, ...]:
    """Run the pending revisions on a throwaway copy and report what is left.

    This is what ``init_db`` does to the real file, so the answer covers every
    revision exactly, including ones that rebuild or rename. The database being
    inspected is only read.
    """
    from alembic import command

    from vendoo_studio.services.schema_migrations import alembic_config

    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / "check.db"
        source = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        destination = sqlite3.connect(copy)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        command.upgrade(alembic_config(f"sqlite:///{copy}"), "head")
        tables, columns = _gaps(_database_schema(copy))
    return tuple(sorted(tables + columns))


def inspect_database(path: str | Path | None = None) -> Drift:
    """Compare the models against the database at ``path``."""
    from alembic.script import ScriptDirectory
    from alembic.util.exc import CommandError

    from vendoo_studio.services.schema_migrations import alembic_config, head_revision

    target = Path(path or DATABASE_PATH)
    if not target.exists():
        raise FileNotFoundError(f"No database at {target}")

    load_models()
    present = _database_schema(target)
    missing_tables, missing_columns = _gaps(present)
    unknown_tables = [
        name for name in present if name not in Base.metadata.tables and name != "alembic_version"
    ]
    drift = Drift(
        missing_tables=tuple(sorted(missing_tables)),
        missing_columns=tuple(sorted(missing_columns)),
        unknown_tables=tuple(sorted(unknown_tables)),
    )

    revision = _recorded_revision(target)
    if revision is None:
        return drift

    scripts = ScriptDirectory.from_config(alembic_config())
    try:
        scripts.get_revision(revision)
    except CommandError:
        return replace(drift, revision=revision, newer_revision=True)
    pending = [
        script.revision
        for script in scripts.iterate_revisions(head_revision(), revision)
        if script.revision != revision
    ]

    if not pending:
        after = (*drift.missing_tables, *drift.missing_columns)
    else:
        after = _gaps_after_migrating(target)
    return replace(
        drift,
        revision=revision,
        pending_revisions=tuple(reversed(pending)),
        after_migrations=after,
    )


def describe(drift: Drift) -> str:
    """A report meant to be read in a terminal by whoever just updated."""
    if drift.newer_revision:
        report = [
            f"This database is at revision {drift.revision}, which this build does not "
            "know. It was written by a newer version of Anteroom; update Anteroom "
            "rather than opening it with this one."
        ]
    elif drift.clean:
        report = ["Database schema matches the models."]
    else:
        report = []
        if drift.missing_tables:
            report.append(f"Missing tables ({len(drift.missing_tables)}):")
            report += [f"  {name}" for name in drift.missing_tables]
        if drift.missing_columns:
            report.append(f"Missing columns ({len(drift.missing_columns)}):")
            report += [f"  {name}" for name in drift.missing_columns]
        report.append("")
        if drift.pending_revisions:
            report.append(
                f"Pending migrations ({len(drift.pending_revisions)}), "
                f"{drift.revision} to {drift.pending_revisions[-1]}: "
                + ", ".join(drift.pending_revisions)
            )
        if drift.healed_by_init_db:
            report.append("Starting Anteroom adds all of these. No action needed.")
        else:
            report.append(
                "Starting Anteroom will NOT add these columns: "
                + ", ".join(drift.unhealed)
                + ". Write an Alembic revision that adds them "
                "(cd vendoo-studio && .venv/bin/alembic revision --autogenerate -m \"...\")."
            )
    if drift.unknown_tables:
        report.append("")
        report.append(
            "Tables not in the models (left alone, listed for information): "
            + ", ".join(drift.unknown_tables)
        )
    return "\n".join(report)
