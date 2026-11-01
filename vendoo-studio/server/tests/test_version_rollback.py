"""Going back to an earlier published build: listing, database checks, and the swap."""

from __future__ import annotations

import gzip
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from vendoo_studio.services import backups, packaged_updates, schema_migrations, updates
from vendoo_studio.services.schema_migrations import (
    SchemaError,
    head_revision,
    needs_older_database,
    rollback_plan,
)


@pytest.fixture
def snapshots(tmp_path, monkeypatch):
    directory = tmp_path / "backups"
    monkeypatch.setattr(backups, "BACKUPS_DIR", str(directory))
    monkeypatch.setenv("VENDOO_STUDIO_DATA_DIR", str(tmp_path / "userdata"))
    return directory


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'studio.db'}")
    command.upgrade(schema_migrations.alembic_config(str(engine.url)), "head")
    return engine


def _previous_revision() -> str:
    script = ScriptDirectory.from_config(schema_migrations.alembic_config())
    down = script.get_revision(head_revision()).down_revision
    # A join migration has several parents; any of them is an older layout.
    if isinstance(down, tuple):
        down = down[0]
    assert isinstance(down, str)
    return down


def _stamped_snapshot(directory: Path, stamp: str, revision: str) -> Path:
    """A gzipped snapshot named the way backups.py names them, stamped ``revision``."""
    directory.mkdir(parents=True, exist_ok=True)
    plain = directory / f"plain-{stamp}.db"
    connection = sqlite3.connect(plain)
    connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    connection.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
    connection.commit()
    connection.close()
    target = directory / f"{backups.PREFIX}-{stamp}-pre-migration.db.gz"
    with open(plain, "rb") as source, gzip.open(target, "wb") as compressed:
        shutil.copyfileobj(source, compressed)
    plain.unlink()
    return target


# Versioned tags


def test_versioned_tag_is_parsed_into_version_and_commit():
    tag = f"{packaged_updates.VERSIONED_TAG_PREFIX}0.1.594-abc1234"
    assert packaged_updates._parse_versioned_tag(tag) == ("0.1.594", "abc1234")
    assert packaged_updates._parse_versioned_tag(packaged_updates.RELEASE_TAG) is None
    assert packaged_updates._parse_versioned_tag("v0.1.594-abc1234") is None


def test_versioned_asset_url_points_at_that_build():
    url = packaged_updates.release_asset_url("build_info.json", "studio-macos-v1.0.0-abc1234")
    assert url.endswith("/releases/download/studio-macos-v1.0.0-abc1234/build_info.json")


def test_published_versions_lists_only_per_build_releases_newest_first(tmp_path, monkeypatch):
    info = tmp_path / "build_info.json"
    info.write_text('{"version": "0.1.595", "sha": "bbb2222ffff"}', encoding="utf-8")
    monkeypatch.setenv("VENDOO_STUDIO_BUILD_INFO", str(info))
    prefix = packaged_updates.VERSIONED_TAG_PREFIX
    releases = [
        {"tag_name": packaged_updates.RELEASE_TAG, "name": "Rolling", "published_at": "2026-10-30T00:00:00Z"},
        {"tag_name": f"{prefix}0.1.594-aaa1111", "name": "Older", "published_at": "2026-10-28T00:00:00Z"},
        {"tag_name": f"{prefix}0.1.595-bbb2222", "name": "Newer", "published_at": "2026-10-29T00:00:00Z"},
        {"tag_name": f"{prefix}0.1.596-ccc3333", "name": "Draft", "draft": True, "published_at": None},
    ]
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=releases)))
    versions = packaged_updates.list_published_versions(client=client)
    assert [item["version"] for item in versions] == ["0.1.595", "0.1.594"]
    assert [item["installed"] for item in versions] == [True, False]
    assert versions[0]["title"] == "Newer"


def test_build_info_carries_the_schema_revision():
    normalized = packaged_updates._normalize_build_info({"sha": "abc", "schema_revision": "d8b6f2a19c40"})
    assert normalized["schema_revision"] == "d8b6f2a19c40"
    assert packaged_updates._normalize_build_info({"sha": "abc"})["schema_revision"] is None


# Database checks


def test_same_or_newer_layout_keeps_the_database(engine):
    assert needs_older_database(head_revision(), engine) is False
    # A revision this build has never heard of belongs to a newer build, which
    # migrates the database forward when it starts.
    assert needs_older_database("f00dfacefeed", engine) is False


def test_older_layout_needs_an_older_database(engine):
    assert needs_older_database(_previous_revision(), engine) is True


def test_build_without_a_layout_is_refused(engine):
    with pytest.raises(SchemaError):
        needs_older_database(None, engine)


def test_rollback_plan_finds_the_newest_snapshot_in_that_layout(engine, snapshots):
    older = _previous_revision()
    _stamped_snapshot(snapshots, "20261001-000000", older)
    newest_match = _stamped_snapshot(snapshots, "20261002-000000", older)
    _stamped_snapshot(snapshots, "20261003-000000", head_revision())
    plan = rollback_plan(older, engine)
    assert plan["action"] == "restore"
    assert plan["snapshot"]["name"] == newest_match.name


def test_rollback_plan_is_blocked_without_a_snapshot_in_that_layout(engine, snapshots):
    _stamped_snapshot(snapshots, "20261003-000000", head_revision())
    plan = rollback_plan(_previous_revision(), engine)
    assert plan["action"] == "blocked"
    assert "no backup" in plan["reason"]


def test_stage_restore_unpacks_and_checks_the_snapshot(tmp_path, snapshots):
    path = _stamped_snapshot(snapshots, "20261002-000000", "abc123")
    snapshot = backups._parse(path)
    database = tmp_path / "live.db"
    staged = backups.stage_restore(snapshot, database)
    assert staged == tmp_path / "live.db.restoring"
    assert backups.database_revision(staged) == "abc123"


# Installing


def test_install_version_is_mac_app_only(monkeypatch):
    monkeypatch.setattr(updates, "is_packaged", lambda: False)
    with pytest.raises(updates.UpdateBlocked, match="Mac app"):
        updates.install_version("studio-macos-v0.1.594-aaa1111")


def test_install_version_needs_the_reviewed_snapshot_for_an_older_layout(monkeypatch):
    for key, value in {"status": "idle", "download_percent": None, "sha": None, "error": None}.items():
        monkeypatch.setitem(updates._operation_state, key, value)
    monkeypatch.setattr(updates, "is_packaged", lambda: True)
    monkeypatch.setattr(packaged_updates, "fetch_version_build_info", lambda tag: {"schema_revision": "older"})
    monkeypatch.setattr(schema_migrations, "needs_older_database", lambda revision, engine=None: True)
    installed = []
    monkeypatch.setattr(packaged_updates, "install_packaged_version", lambda *a, **k: installed.append(a))
    with pytest.raises(updates.UpdateBlocked, match="earlier backup"):
        updates.install_version("studio-macos-v0.1.594-aaa1111", None)
    assert installed == []
    assert updates.update_progress()["status"] == "error"


def test_replacer_puts_the_restored_database_in_place(tmp_path, monkeypatch):
    monkeypatch.setenv("VENDOO_STUDIO_DATA_DIR", str(tmp_path / "data"))
    app = tmp_path / "Anteroom.app"
    new_app = tmp_path / "new" / "Anteroom.app"
    live = tmp_path / "db" / "vendoo_studio.db"
    staged = tmp_path / "db" / "vendoo_studio.db.restoring"
    script = packaged_updates._write_replacer(app, new_app, 12345, restore_database=(staged, live))
    text = script.read_text(encoding="utf-8")
    assert f"-wal {live}-shm" in text
    assert f"/bin/mv {staged} {live}" in text
    # The database moves only once the old app is gone, right before relaunch.
    assert text.index(f"/bin/mv {staged}") > text.index("kill -0")
    assert text.index(f"/bin/mv {staged}") < text.index("/usr/bin/open")


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("ditto"), reason="needs macOS ditto")
def test_replacer_swaps_the_database_and_drops_the_old_wal(tmp_path, monkeypatch):
    monkeypatch.setenv("VENDOO_STUDIO_DATA_DIR", str(tmp_path / "data"))
    app = tmp_path / "Anteroom.app"
    new_app = tmp_path / "new" / "Anteroom.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (new_app / "Contents" / "MacOS").mkdir(parents=True)
    (new_app / "Contents" / "MacOS" / "Anteroom").write_text("old build", encoding="utf-8")
    live = tmp_path / "vendoo_studio.db"
    live.write_text("newer layout", encoding="utf-8")
    Path(f"{live}-wal").write_text("pending writes", encoding="utf-8")
    staged = tmp_path / "vendoo_studio.db.restoring"
    staged.write_text("older layout", encoding="utf-8")
    script = packaged_updates._write_replacer(app, new_app, 999999, restore_database=(staged, live))
    text = script.read_text(encoding="utf-8").replace("/usr/bin/open ", "/usr/bin/true ")
    subprocess.run(["/bin/bash", "-c", text], check=True)
    assert live.read_text(encoding="utf-8") == "older layout"
    assert not Path(f"{live}-wal").exists()
    assert not staged.exists()
