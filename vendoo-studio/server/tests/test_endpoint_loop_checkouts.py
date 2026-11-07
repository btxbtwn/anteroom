"""Async handlers keep database work off the event loop and hold no connection across slow waits."""
import asyncio
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import Pool, QueuePool

from loop_checkouts import EndpointCheckouts
from vendoo_studio.database import in_worker_session

SLOW_EXTERNAL_S = 0.3


def _one_connection_pool(tmp_path):
    # A pool of one, failing fast like the hosted pool: any connection held across
    # a slow wait leaves the next request nothing.
    return create_engine(f"sqlite:///{tmp_path / 'pool.db'}", poolclass=QueuePool, pool_size=1, max_overflow=0,
                         pool_timeout=0.2, connect_args={"check_same_thread": False})


def _app(sessions) -> FastAPI:
    app = FastAPI()

    def read(db):
        return db.execute(text("SELECT 1")).scalar()

    @app.get("/released")
    async def released():
        before = await in_worker_session(read)
        await asyncio.sleep(SLOW_EXTERNAL_S)  # Chrome, a marketplace or a model
        after = await in_worker_session(read)
        return {"ok": before == after == 1}

    @app.get("/held")
    async def held():
        with sessions() as db:
            db.execute(text("SELECT 1"))
            await asyncio.sleep(SLOW_EXTERNAL_S)
            return {"ok": True}

    return app


async def _concurrently(app, path, count):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await asyncio.gather(*(client.get(path) for _ in range(count)), return_exceptions=True)


def test_slow_waits_do_not_exhaust_a_small_pool(tmp_path):
    engine = _one_connection_pool(tmp_path)
    sessions = sessionmaker(bind=engine)
    with patch("vendoo_studio.database.SessionLocal", sessions):
        replies = asyncio.run(_concurrently(_app(sessions), "/released", 8))
    assert [reply.json() for reply in replies] == [{"ok": True}] * 8
    engine.dispose()


def test_holding_a_session_across_a_slow_wait_does_exhaust_it(tmp_path):
    """The failure the hosted API saw, reproduced so the test above means something."""
    engine = _one_connection_pool(tmp_path)
    sessions = sessionmaker(bind=engine)
    with patch("vendoo_studio.database.SessionLocal", sessions):
        replies = asyncio.run(_concurrently(_app(sessions), "/held", 4))
    assert any(isinstance(reply, PoolTimeout) for reply in replies)
    engine.dispose()


@pytest.mark.parametrize(("path", "flagged"), [("/released", False), ("/held", True)])
def test_the_suite_notices_a_query_on_the_event_loop(tmp_path, path, flagged):
    engine = _one_connection_pool(tmp_path)
    sessions = sessionmaker(bind=engine)
    watcher = EndpointCheckouts(root=Path(__file__).parent)
    event.listen(Pool, "checkout", watcher.checkout)
    try:
        with patch("vendoo_studio.database.SessionLocal", sessions):
            asyncio.run(_concurrently(_app(sessions), path, 1))
    finally:
        event.remove(Pool, "checkout", watcher.checkout)
        engine.dispose()
    assert bool(watcher.found) is flagged
