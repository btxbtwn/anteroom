"""The stopped-app restore CLI must refuse corrupt disposable snapshots safely."""
import gzip
import hashlib
import os
from pathlib import Path
import socket
import sqlite3
import subprocess

import pytest

pytestmark = pytest.mark.sqlite_only("restore-db.sh replaces only a stopped local SQLite file")


def _anteroom_database(path: Path, value: str) -> None:
    """A minimal database stamped like one Anteroom wrote."""
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE alembic_version(version_num TEXT NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES ('fixture_revision')")
        connection.execute("CREATE TABLE fixture(value TEXT)")
        connection.execute("INSERT INTO fixture VALUES (?)", (value,))


def _restore(snapshot: Path, data: Path):
    # Pick an unused loopback port so the real app is never probed.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = {**os.environ, "ANTEROOM_DATA_DIR": str(data), "ANTEROOM_SANDBOX": "1", "ANTEROOM_PORT": str(port)}
    env.pop("ANTEROOM_DATABASE_URL", None)
    script = Path(__file__).resolve().parents[2] / "scripts" / "restore-db.sh"
    return subprocess.run([str(script), str(snapshot), "--yes"], capture_output=True, text=True, env=env, timeout=20)


@pytest.mark.parametrize("format", ["plain", "compressed", "broken-gzip", "empty", "empty-compressed", "foreign-sqlite",
                                    "valid-plain", "valid-compressed"])
def test_restore_validates_snapshot_before_replacing_database(tmp_path, format):
    data = tmp_path / "disposable-data"
    data.mkdir()
    database = data / "anteroom.db"
    _anteroom_database(database, "keep this fixture")
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    valid = format.startswith("valid-")
    snapshot = tmp_path / ("snapshot.db.gz" if format in {"compressed", "broken-gzip", "empty-compressed", "valid-compressed"} else "snapshot.db")
    if valid:
        source = tmp_path / "source.db"
        _anteroom_database(source, "restored fixture")
        snapshot.write_bytes(gzip.compress(source.read_bytes()) if format == "valid-compressed" else source.read_bytes())
    elif format == "empty":
        snapshot.write_bytes(b"")
    elif format == "empty-compressed":
        snapshot.write_bytes(gzip.compress(b""))
    elif format == "foreign-sqlite":
        # Passes integrity_check, but nothing in it is Anteroom's.
        with sqlite3.connect(snapshot) as connection:
            connection.execute("CREATE TABLE fixture(value TEXT)")
    else:
        snapshot.write_bytes(gzip.compress(b"not a SQLite database") if format == "compressed" else b"not a database or gzip")
    result = _restore(snapshot, data)
    if valid:
        assert result.returncode == 0, result.stderr
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT value FROM fixture").fetchone()[0] == "restored fixture"
        saved = list((data / "backups").glob("*pre-restore.db.gz"))
        assert len(saved) == 1
        earlier = tmp_path / "pre-restore.db"
        earlier.write_bytes(gzip.decompress(saved[0].read_bytes()))
        with sqlite3.connect(earlier) as connection:
            assert connection.execute("SELECT value FROM fixture").fetchone()[0] == "keep this fixture"
    else:
        assert result.returncode == 65, result.stderr
        assert "Choose another snapshot" in result.stderr
        assert "current database was not changed" in result.stderr
        assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert "Traceback" not in result.stderr
    assert not list(data.glob("anteroom.db.restoring.*"))


@pytest.mark.parametrize("alias", ["staging", "hardlink", "symlink", "unreadable"])
def test_restore_preserves_input_aliases_and_explains_filesystem_errors(tmp_path, alias):
    if alias == "unreadable" and os.geteuid() == 0:
        pytest.skip("root can read mode-000 files")
    data = tmp_path / "disposable-data"
    data.mkdir()
    database = data / "anteroom.db"
    _anteroom_database(database, "keep this fixture")
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    staged = Path(f"{database}.restoring")
    staged.write_bytes(database.read_bytes())
    snapshot = staged
    if alias in {"hardlink", "symlink"}:
        snapshot = tmp_path / "snapshot.db"
        if alias == "hardlink":
            snapshot.hardlink_to(staged)
        else:
            snapshot.symlink_to(staged)
    if alias == "unreadable":
        snapshot = tmp_path / "snapshot.db"
        staged.unlink()
        snapshot.write_bytes(database.read_bytes())
        snapshot.chmod(0)
    result = _restore(snapshot, data)
    if alias == "unreadable":
        snapshot.chmod(0o600)
        assert result.returncode == 74, result.stderr
        assert "Check file permissions" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert "Restored." in result.stdout
    if alias == "unreadable":
        assert "current database was not changed" in result.stderr
    assert "Traceback" not in result.stderr
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert snapshot.exists()
    assert not list(data.glob("anteroom.db.restoring.*"))
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("alias", ["hardlink", "symlink"])
def test_corrupt_restore_never_follows_old_staging_alias_to_current_database(tmp_path, alias):
    data = tmp_path / "disposable-data"
    data.mkdir()
    database = data / "anteroom.db"
    _anteroom_database(database, "keep this fixture")
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    old_staging = Path(f"{database}.restoring")
    if alias == "hardlink":
        old_staging.hardlink_to(database)
    else:
        old_staging.symlink_to(database)
    snapshot = tmp_path / "corrupt.db"
    snapshot.write_bytes(b"not a database")
    result = _restore(snapshot, data)
    assert result.returncode == 65, result.stderr
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert snapshot.read_bytes() == b"not a database"
    assert "current database was not changed" in result.stderr
