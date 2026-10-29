from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest


def _postgres_database(worker: str) -> None:
    """Point the app at a fresh Postgres database for this worker.

    Set ``ANTEROOM_TEST_DATABASE_URL`` to a server URL (any database on it) to
    run the suite on Postgres; each xdist worker gets its own database there.
    """
    import psycopg
    from sqlalchemy.engine import make_url

    base = make_url(os.environ["ANTEROOM_TEST_DATABASE_URL"])
    name = f"anteroom_test_{worker}"
    admin = base.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        connection.execute(f'CREATE DATABASE "{name}"')
    os.environ["ANTEROOM_DATABASE_URL"] = base.set(database=name).render_as_string(hide_password=False)


def pytest_configure(config):
    """Give each xdist worker its own data dir before vendoo_studio.config is imported."""
    config.addinivalue_line("markers", "sqlite_only(reason): needs the Mac's SQLite database file")
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if os.environ.get("ANTEROOM_TEST_DATABASE_URL"):
        _postgres_database(worker or "main")
    if not worker:
        return
    base = Path(os.environ.get("VENDOO_STUDIO_DATA_DIR") or tempfile.mkdtemp(prefix="vendoo-studio-tests-"))
    worker_dir = base / worker
    worker_dir.mkdir(parents=True, exist_ok=True)
    os.environ["VENDOO_STUDIO_DATA_DIR"] = str(worker_dir)


def pytest_collection_modifyitems(config, items):
    if not os.environ.get("ANTEROOM_TEST_DATABASE_URL"):
        return
    for item in items:
        marker = item.get_closest_marker("sqlite_only")
        if marker is not None:
            reason = marker.args[0] if marker.args else "needs the Mac's SQLite database file"
            item.add_marker(pytest.mark.skip(reason=f"SQLite only: {reason}"))


@pytest.fixture(scope="session", autouse=True)
def _local_test_client_origin():
    """Exercise the same Host checks as the real loopback app, not testserver."""
    from unittest.mock import patch
    from starlette.testclient import TestClient

    original = TestClient.__init__
    original_websocket = TestClient.websocket_connect

    def initialize(self, *args, **kwargs):
        if len(args) < 2:
            kwargs.setdefault("base_url", "http://127.0.0.1:4318")
        original(self, *args, **kwargs)

    def websocket(self, url, *args, **kwargs):
        if url.startswith("/"):
            url = str(self.base_url.copy_with(scheme="ws")).rstrip("/") + url
        return original_websocket(self, url, *args, **kwargs)

    with patch.object(TestClient, "__init__", initialize), patch.object(TestClient, "websocket_connect", websocket):
        yield


@pytest.fixture(scope="session", autouse=True)
def _isolate_studio_settings(tmp_path_factory):
    previous = os.environ.get("VENDOO_STUDIO_DATA_DIR")
    root = tmp_path_factory.mktemp("studio-settings")
    os.environ["VENDOO_STUDIO_DATA_DIR"] = str(root)
    (root / "settings.json").write_text(json.dumps({
        "marketplaces": ["ebay", "poshmark", "mercari", "depop"],
    }) + "\n")
    # Code paths such as queue dispatch open the app's own SessionLocal. Create its
    # schema up front like app startup does, so results do not depend on test order.
    from vendoo_studio.database import init_db

    init_db()
    yield
    if previous is None:
        os.environ.pop("VENDOO_STUDIO_DATA_DIR", None)
    else:
        os.environ["VENDOO_STUDIO_DATA_DIR"] = previous


@pytest.fixture(scope="session", autouse=True)
def _no_sourcing_timer():
    """Keep app startup from refreshing Sourcing in the background.

    A test that enters the app's lifespan (``with TestClient(app)``) would start
    the timer, which crawls the live supplier sites at once. Shutdown cannot
    interrupt a refresh underway, so it went on writing ``sourcing.json`` into
    whichever test's data directory was current, and that test's cleanup failed
    with "Directory not empty". Sourcing tests call ``refresh`` themselves.
    """
    from unittest import mock

    from vendoo_studio.services import box_scout

    with mock.patch.object(box_scout, "start_sourcing_timer", return_value=None):
        yield


@pytest.fixture(scope="session", autouse=True)
def _no_category_fills():
    """Keep app startup from arming the category field fill.

    With it armed, a test that saves a new marketplace category through the app
    would queue a model call after the test ended. Its own tests arm it themselves.
    """
    from unittest import mock

    from vendoo_studio.services import native_field_fill

    with mock.patch.object(native_field_fill, "start_category_fills", return_value=None):
        yield


@pytest.fixture(autouse=True)
def _fresh_business_context():
    """Each test reads its own shop, never the assistant's copy from the test before."""
    from vendoo_studio.services.business_context import invalidate_business_context

    invalidate_business_context()
    yield
    invalidate_business_context()


@pytest.fixture(autouse=True)
def _claude_signed_out(monkeypatch):
    """Keep the machine's own Claude Code login out of tests unless one opts in."""
    from vendoo_studio.services import claude_auth

    monkeypatch.setattr(claude_auth, "claude_cli_path", lambda: None)
    claude_auth.forget_status()
    yield
    claude_auth.forget_status()


@pytest.fixture(autouse=True)
def _no_real_keychain():
    """Give every test an empty in-memory keyring instead of the login Keychain.

    The real Keychain does not trust the test interpreter, so each read pops a
    "Python wants to use vendoo-studio" password dialog — one per read, per
    xdist worker, every time the suite runs.
    """
    import keyring
    from keyring.backend import KeyringBackend

    class MemoryKeyring(KeyringBackend):
        priority = 1

        def __init__(self) -> None:
            super().__init__()
            self.items: dict[tuple[str, str], str] = {}

        def get_password(self, service, username):
            return self.items.get((service, username))

        def set_password(self, service, username, password):
            self.items[(service, username)] = password

        def delete_password(self, service, username):
            self.items.pop((service, username), None)

    original = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    yield
    keyring.set_keyring(original)


@pytest.fixture(autouse=True)
def _keychain_memory_is_pristine():
    """Leave the keychain module's in-memory state as a fresh process has it.

    A test that reads a secret without a fake keyring leaves ``_store`` set,
    and a later test's setter would flush it. ``RealKeychainIsUntouchedTest``
    checks for exactly that, and with xdist the test that leaked can be any
    test the worker ran first, so the state is reset around every test.
    """
    from vendoo_studio.services import keychain

    def reset() -> None:
        keychain._store = None
        keychain._store_readable = False
        keychain._touched.clear()
        keychain._deleted.clear()

    reset()
    yield
    reset()
