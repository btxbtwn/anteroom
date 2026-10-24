from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from vendoo_studio import config


def _load_make_icon() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "desktop" / "make_icon.py"
    spec = importlib.util.spec_from_file_location("make_icon", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ConfigPathTest(unittest.TestCase):
    def setUp(self) -> None:
        self.keys = (
            "VENDOO_STUDIO_DATA_DIR",
            "VENDOO_STUDIO_RESOURCE_DIR",
            "VENDOO_STUDIO_PACKAGED",
            "VENDOO_STUDIO_SKILLS_DIR",
            "VENDOO_STUDIO_EXTENSION_DIR",
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
            os.environ["VENDOO_STUDIO_DATA_DIR"] = tmp
            self.assertEqual(config.user_data_root(), Path(tmp).resolve())

    def test_packaged_flag_from_env(self):
        os.environ.pop("VENDOO_STUDIO_PACKAGED", None)
        self.assertFalse(config.is_packaged())
        os.environ["VENDOO_STUDIO_PACKAGED"] = "1"
        self.assertTrue(config.is_packaged())

    def test_packaged_user_data_lives_in_application_support(self):
        os.environ["VENDOO_STUDIO_PACKAGED"] = "1"
        os.environ.pop("VENDOO_STUDIO_DATA_DIR", None)
        self.assertEqual(
            config.user_data_root(),
            Path.home() / "Library" / "Application Support" / "Anteroom",
        )

    def test_pre_rename_data_folder_moves_and_stays_reachable(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            support = home / "Library" / "Application Support"
            legacy = support / config.LEGACY_APP_NAME
            (legacy / "vendoo-extension").mkdir(parents=True)
            (legacy / "vendoo_studio.db").write_text("db", encoding="utf-8")
            os.environ["VENDOO_STUDIO_PACKAGED"] = "1"
            os.environ.pop("VENDOO_STUDIO_DATA_DIR", None)
            with patch.object(Path, "home", return_value=home):
                config.adopt_legacy_data_dir()
                config.adopt_legacy_data_dir()
            target = support / config.APP_NAME
            self.assertFalse(target.is_symlink())
            self.assertEqual((target / "vendoo_studio.db").read_text(encoding="utf-8"), "db")
            self.assertTrue(legacy.is_symlink())
            self.assertTrue((legacy / "vendoo-extension").is_dir())

    def test_fresh_install_makes_no_legacy_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            os.environ["VENDOO_STUDIO_PACKAGED"] = "1"
            os.environ.pop("VENDOO_STUDIO_DATA_DIR", None)
            with patch.object(Path, "home", return_value=home):
                config.adopt_legacy_data_dir()
            self.assertFalse((home / "Library" / "Application Support" / config.LEGACY_APP_NAME).exists())

    def test_log_path_lives_under_user_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["VENDOO_STUDIO_DATA_DIR"] = tmp
            self.assertEqual(
                config.log_path(),
                Path(tmp).resolve() / "logs" / "Anteroom.log",
            )

    def test_skills_dir_finds_repo_skill(self):
        os.environ.pop("VENDOO_STUDIO_SKILLS_DIR", None)
        skill = config.skills_dir() / "list-this" / "SKILL.md"
        self.assertTrue(skill.is_file(), skill)

    def test_extension_dir_has_manifest_and_icons(self):
        os.environ.pop("VENDOO_STUDIO_EXTENSION_DIR", None)
        extension = config.extension_source_dir()
        self.assertTrue((extension / "manifest.json").is_file(), extension)
        for name in ("icon16.png", "icon32.png", "icon48.png", "icon128.png"):
            self.assertTrue((extension / "icons" / name).is_file(), name)

    def test_icon_files_match_the_drawing(self):
        from PIL import Image

        make_icon = _load_make_icon()
        icons = Path(__file__).resolve().parents[3] / "vendoo-extension" / "icons"
        for size in make_icon.EXTENSION_ICON_SIZES:
            chrome = Image.open(icons / f"icon{size}.png").convert("RGBA")
            self.assertEqual(chrome.tobytes(), make_icon.render_extension_icon(size).tobytes(), size)
        favicon = Path(__file__).resolve().parents[2] / "public" / "favicon.png"
        self.assertEqual(Image.open(favicon).convert("RGBA").tobytes(), make_icon.render_icon(32).tobytes())
        app = Image.open(make_icon.APP_ICON_PNG).convert("RGBA")
        self.assertEqual(app.tobytes(), make_icon.render_icon(1024).tobytes())

    def test_app_icon_stays_inside_apples_tile(self):
        # macOS 26 shrinks icons that do not fit its tile onto a grey backing.
        make_icon = _load_make_icon()
        alpha = make_icon.render_icon(1024).getchannel("A")
        self.assertEqual(alpha.getbbox(), (100, 100, 924, 924))
        self.assertEqual(alpha.getpixel((512, 512)), 255)
