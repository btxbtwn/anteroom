"""A Chrome wait must not start while its task holds a pooled database connection."""
import asyncio

import pytest
from sqlalchemy import text

from vendoo_studio.database import SessionLocal
from vendoo_studio.services import chrome_wait_connections
from vendoo_studio.services.extension_connection import ReplyWaits


def _select_one():
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))


def test_a_wait_with_an_open_transaction_is_refused():
    async def wait_holding():
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            ReplyWaits().register_wait("held")

    with pytest.raises(chrome_wait_connections.ConnectionHeldDuringChromeWait):
        asyncio.run(wait_holding())


def test_a_wait_after_the_transaction_ends_is_allowed():
    async def wait_released():
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            db.commit()
            ReplyWaits().register_wait("released")
        # Reads handed to a worker thread are not the waiting task's.
        await asyncio.to_thread(_select_one)
        ReplyWaits().register_wait("after-thread")

    asyncio.run(wait_released())


def test_production_logs_each_place_once_instead_of_failing(monkeypatch, caplog):
    monkeypatch.setattr(chrome_wait_connections, "strict", False)
    monkeypatch.setattr(chrome_wait_connections, "_seen", set())

    async def wait_holding():
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            for _ in range(2):
                ReplyWaits().register_wait("held")

    asyncio.run(wait_holding())
    warnings = [record for record in caplog.records if "held while waiting on Chrome" in record.getMessage()]
    assert len(warnings) == 1


def test_the_hosted_app_leaves_a_watch_it_did_not_start():
    from fastapi.testclient import TestClient

    from vendoo_studio.database import engine
    from vendoo_studio.hosted import create_app

    with TestClient(create_app('https://example.supabase.co')):
        pass
    # The session-wide watch from conftest still catches a held connection afterwards.
    test_a_wait_with_an_open_transaction_is_refused()
    assert not chrome_wait_connections.watch(engine)
