"""The packaged smoke must keep an older installed build off the seller's data.

The audit runs the script from ``main`` against the installed Mac app, which
lags ``main``. Builds before the rename (0.1.1053 and earlier) read their data
folder and port only from the old variable names; given just the new ones they
opened the seller's real data folder and port 4318 (daily audit 2026-11-20).
"""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "smoke-packaged-sandbox.py"


def _script():
    spec = importlib.util.spec_from_file_location("smoke_packaged_sandbox", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sandbox_env_points_old_and_new_builds_at_the_throwaway_folder(tmp_path):
    env = _script().sandbox_env(str(tmp_path), base={})

    assert env["ANTEROOM_SANDBOX"] == "1"
    for name in ("ANTEROOM_DATA_DIR", "VENDOO_STUDIO_DATA_DIR"):
        assert env[name] == str(tmp_path)
    for name in ("ANTEROOM_PORT", "VENDOO_STUDIO_PORT"):
        assert env[name] == "4321"


def test_sandbox_env_overrides_inherited_values(tmp_path):
    inherited = {"VENDOO_STUDIO_DATA_DIR": "/real/data", "ANTEROOM_PORT": "4318", "PATH": "/usr/bin"}

    env = _script().sandbox_env(str(tmp_path), base=inherited)

    assert env["VENDOO_STUDIO_DATA_DIR"] == str(tmp_path)
    assert env["ANTEROOM_PORT"] == "4321"
    assert env["PATH"] == "/usr/bin"
