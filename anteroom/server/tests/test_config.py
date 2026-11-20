from __future__ import annotations

import importlib.util
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from anteroom import config


def _load_make_icon() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "desktop" / "make_icon.py"
    spec = importlib.util.spec_from_file_location("make_icon", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_sqlite_with_committed_row(db_path: Path, value: str = "kept") -> None:
    """Temp-dir SQLite only: committed row, connection closed. Never a live seller DB."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE item (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        conn.execute("INSERT INTO item (name) VALUES (?)", (value,))
        conn.commit()
    finally:
        conn.close()


def _open_sqlite_with_uncheckpointed_wal(db_path: Path, value: str) -> sqlite3.Connection:
    """Open temp SQLite with a committed row still in the WAL (autocheckpoint off)."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE item (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    conn.execute("INSERT INTO item (name) VALUES (?)", (value,))
    conn.commit()
    return conn


def _read_sqlite_names(db_path: Path) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return [row[0] for row in conn.execute("SELECT name FROM item ORDER BY id")]
    finally:
        conn.close()


class ConfigPathTest(unittest.TestCase):
    def setUp(self) -> None:
        self.keys = (
            "ANTEROOM_DATA_DIR",
            "ANTEROOM_RESOURCE_DIR",
            "ANTEROOM_PACKAGED",
            "ANTEROOM_SKILLS_DIR",
            "ANTEROOM_EXTENSION_DIR",
        )
        self.saved = {key: os.environ.get(key) for key in self.keys}

    def tearDown(self) -> None:
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_user_data_dir_uses_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["ANTEROOM_DATA_DIR"] = tmp
            self.assertEqual(config.user_data_root(), Path(tmp).resolve())

    def test_packaged_flag_from_env(self):
        os.environ.pop("ANTEROOM_PACKAGED", None)
        self.assertFalse(config.is_packaged())
        os.environ["ANTEROOM_PACKAGED"] = "1"
        self.assertTrue(config.is_packaged())

    def test_packaged_user_data_lives_in_application_support(self):
        os.environ["ANTEROOM_PACKAGED"] = "1"
        os.environ.pop("ANTEROOM_DATA_DIR", None)
        self.assertEqual(
            config.user_data_root(),
            Path.home() / "Library" / "Application Support" / "Anteroom",
        )

    def test_pre_rename_data_folder_moves_and_stays_reachable(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            support = home / "Library" / "Application Support"
            legacy = support / config.LEGACY_APP_NAME
            (legacy / "anteroom-extension").mkdir(parents=True)
            (legacy / "anteroom.db").write_text("db", encoding="utf-8")
            os.environ["ANTEROOM_PACKAGED"] = "1"
            os.environ.pop("ANTEROOM_DATA_DIR", None)
            with patch.object(Path, "home", return_value=home):
                config.adopt_legacy_data_dir()
                config.adopt_legacy_data_dir()
            target = support / config.APP_NAME
            self.assertFalse(target.is_symlink())
            self.assertEqual((target / "anteroom.db").read_text(encoding="utf-8"), "db")
            self.assertTrue(legacy.is_symlink())
            self.assertTrue((legacy / "anteroom-extension").is_dir())

    def test_fresh_install_makes_no_legacy_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            os.environ["ANTEROOM_PACKAGED"] = "1"
            os.environ.pop("ANTEROOM_DATA_DIR", None)
            with patch.object(Path, "home", return_value=home):
                config.adopt_legacy_data_dir()
            self.assertFalse((home / "Library" / "Application Support" / config.LEGACY_APP_NAME).exists())

    def test_pre_rename_database_moves_sqlite_with_committed_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            legacy = data / "vendoo_studio.db"
            _write_sqlite_with_committed_row(legacy, "row-one")
            os.environ.pop("ANTEROOM_DATABASE_URL", None)
            config.adopt_legacy_database(data)
            config.adopt_legacy_database(data)
            target = data / "anteroom.db"
            self.assertTrue(target.is_file())
            self.assertFalse(legacy.exists())
            self.assertFalse((data / config.LEGACY_DATABASE_ADOPTION_MARKER).exists())
            self.assertEqual(_read_sqlite_names(target), ["row-one"])

    def test_marker_proven_interruption_finishes_sidecars_and_keeps_rows(self):
        """Crash after marker + main rename: finish sidecars; committed rows survive."""
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            legacy = data / "vendoo_studio.db"
            # Keep the connection open so close does not checkpoint the WAL away before
            # we rename only the main file (the interruption window).
            conn = _open_sqlite_with_uncheckpointed_wal(legacy, "from-wal")
            try:
                (data / config.LEGACY_DATABASE_ADOPTION_MARKER).write_text(
                    config.LEGACY_DATABASE_ADOPTION_MARKER_BODY,
                    encoding="utf-8",
                )
                legacy.rename(data / "anteroom.db")
                self.assertTrue((data / "vendoo_studio.db-wal").is_file())
            finally:
                conn.close()
            os.environ.pop("ANTEROOM_DATABASE_URL", None)
            config.adopt_legacy_database(data)
            config.adopt_legacy_database(data)
            self.assertFalse((data / config.LEGACY_DATABASE_ADOPTION_MARKER).exists())
            for suffix in ("", "-wal", "-shm", "-journal"):
                self.assertFalse((data / f"vendoo_studio.db{suffix}").exists())
            self.assertEqual(_read_sqlite_names(data / "anteroom.db"), ["from-wal"])

    def test_ambiguous_new_primary_with_orphan_legacy_wal_is_left_alone(self):
        """anteroom.db existing is not provenance: do not attach an unmarked old WAL."""
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            _write_sqlite_with_committed_row(data / "anteroom.db", "new-primary")
            new_wal = data / "anteroom.db-wal"
            new_wal_before = new_wal.read_bytes() if new_wal.is_file() else None
            orphan = data / "vendoo_studio.db-wal"
            orphan.write_bytes(b"not-this-primary-wal")
            os.environ.pop("ANTEROOM_DATABASE_URL", None)
            config.adopt_legacy_database(data)
            self.assertEqual(_read_sqlite_names(data / "anteroom.db"), ["new-primary"])
            self.assertEqual(orphan.read_bytes(), b"not-this-primary-wal")
            if new_wal_before is not None:
                self.assertEqual(new_wal.read_bytes(), new_wal_before)
            self.assertFalse((data / config.LEGACY_DATABASE_ADOPTION_MARKER).exists())

    def test_ambiguous_both_primaries_are_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            _write_sqlite_with_committed_row(data / "anteroom.db", "new")
            _write_sqlite_with_committed_row(data / "vendoo_studio.db", "old")
            os.environ.pop("ANTEROOM_DATABASE_URL", None)
            config.adopt_legacy_database(data)
            self.assertEqual(_read_sqlite_names(data / "anteroom.db"), ["new"])
            self.assertEqual(_read_sqlite_names(data / "vendoo_studio.db"), ["old"])
            self.assertFalse((data / config.LEGACY_DATABASE_ADOPTION_MARKER).exists())

    def test_marker_with_both_primaries_stops_without_consuming_either(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            _write_sqlite_with_committed_row(data / "anteroom.db", "new")
            _write_sqlite_with_committed_row(data / "vendoo_studio.db", "old")
            marker = data / config.LEGACY_DATABASE_ADOPTION_MARKER
            marker.write_text(config.LEGACY_DATABASE_ADOPTION_MARKER_BODY, encoding="utf-8")
            os.environ.pop("ANTEROOM_DATABASE_URL", None)
            config.adopt_legacy_database(data)
            self.assertFalse(marker.exists())
            self.assertEqual(_read_sqlite_names(data / "anteroom.db"), ["new"])
            self.assertEqual(_read_sqlite_names(data / "vendoo_studio.db"), ["old"])

    def test_competing_wals_abandon_marker_so_later_start_cannot_attach(self):
        """After a WAL conflict, dropping the new WAL must not adopt the legacy one."""
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            _write_sqlite_with_committed_row(data / "anteroom.db", "new-primary")
            # Force both sidecar names present (SQLite may have checkpointed its own WAL away).
            new_wal = data / "anteroom.db-wal"
            new_wal.write_bytes(b"new-wal")
            orphan = data / "vendoo_studio.db-wal"
            orphan.write_bytes(b"competing-legacy-wal")
            marker = data / config.LEGACY_DATABASE_ADOPTION_MARKER
            marker.write_text(config.LEGACY_DATABASE_ADOPTION_MARKER_BODY, encoding="utf-8")
            os.environ.pop("ANTEROOM_DATABASE_URL", None)
            config.adopt_legacy_database(data)
            self.assertFalse(marker.exists())
            self.assertEqual(orphan.read_bytes(), b"competing-legacy-wal")
            self.assertEqual(new_wal.read_bytes(), b"new-wal")
            # Simulate SQLite checkpointing away the new WAL, then another startup.
            new_wal.unlink()
            config.adopt_legacy_database(data)
            self.assertEqual(orphan.read_bytes(), b"competing-legacy-wal")
            self.assertFalse((data / "anteroom.db-wal").exists())
            self.assertEqual(_read_sqlite_names(data / "anteroom.db"), ["new-primary"])

    def test_log_path_lives_under_user_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["ANTEROOM_DATA_DIR"] = tmp
            self.assertEqual(
                config.log_path(),
                Path(tmp).resolve() / "logs" / "Anteroom.log",
            )

    def test_skills_dir_finds_repo_skill(self):
        os.environ.pop("ANTEROOM_SKILLS_DIR", None)
        skill = config.skills_dir() / "list-this" / "SKILL.md"
        self.assertTrue(skill.is_file(), skill)

    def test_extension_dir_has_manifest_and_icons(self):
        os.environ.pop("ANTEROOM_EXTENSION_DIR", None)
        extension = config.extension_source_dir()
        self.assertTrue((extension / "manifest.json").is_file(), extension)
        for name in ("icon16.png", "icon32.png", "icon48.png", "icon128.png"):
            self.assertTrue((extension / "icons" / name).is_file(), name)

    def test_icon_files_match_the_drawing(self):
        from PIL import Image

        make_icon = _load_make_icon()
        icons = Path(__file__).resolve().parents[3] / "anteroom-extension" / "icons"
        for size in make_icon.EXTENSION_ICON_SIZES:
            chrome = Image.open(icons / f"icon{size}.png").convert("RGBA")
            self.assertEqual(chrome.tobytes(), make_icon.render_extension_icon(size).tobytes(), size)
        favicon = Path(__file__).resolve().parents[2] / "public" / "favicon.png"
        self.assertEqual(Image.open(favicon).convert("RGBA").tobytes(), make_icon.render_icon(32).tobytes())
        app = Image.open(make_icon.APP_ICON_PNG).convert("RGBA")
        self.assertEqual(app.tobytes(), make_icon.render_icon(1024).tobytes())
        ios = Image.open(make_icon.IOS_APP_ICON)
        self.assertEqual(ios.mode, "RGB")
        self.assertEqual(ios.tobytes(), make_icon.render_ios_icon().tobytes())

    def test_icon_arches_fade_out_at_the_floor(self):
        make_icon = _load_make_icon()
        icon = make_icon.render_icon(1024)
        door = (512, make_icon.FADE_TOP - 40)
        floor = (512, make_icon.FLOOR - 4)
        self.assertEqual(icon.getpixel(door)[:3], make_icon.DOOR[:3])
        # Just above the floor the doorway has almost faded into the tile.
        self.assertLess(icon.getpixel(floor)[0], 40)

    def test_app_icon_stays_inside_apples_tile(self):
        # macOS 26 shrinks icons that do not fit its tile onto a grey backing.
        make_icon = _load_make_icon()
        alpha = make_icon.render_icon(1024).getchannel("A")
        self.assertEqual(alpha.getbbox(), (100, 100, 924, 924))
        self.assertEqual(alpha.getpixel((512, 512)), 255)
