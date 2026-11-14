"""Async handlers keep database work off the event loop and hold no connection across slow waits."""
import asyncio
import time
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
from vendoo_studio.database import claim_in_worker_session, in_worker_session

SLOW_EXTERNAL_S = 0.3


def _one_connection_pool(tmp_path, *, pool_timeout=0.2):
    # A pool of one, failing fast like the hosted pool: any connection held across
    # a slow wait leaves the next request nothing.
    return create_engine(f"sqlite:///{tmp_path / 'pool.db'}", poolclass=QueuePool, pool_size=1, max_overflow=0,
                         pool_timeout=pool_timeout, connect_args={"check_same_thread": False})


async def _external_pause():
    await asyncio.sleep(SLOW_EXTERNAL_S)


def _app(sessions, external_wait=_external_pause) -> FastAPI:
    app = FastAPI()

    def read(db):
        return db.execute(text("SELECT 1")).scalar()

    @app.get("/released")
    async def released():
        before = await in_worker_session(read)
        await external_wait()  # Chrome, a marketplace or a model
        after = await in_worker_session(read)
        return {"ok": before == after == 1}

    @app.get("/held")
    async def held():
        with sessions() as db:
            db.execute(text("SELECT 1"))
            await external_wait()
            return {"ok": True}

    return app


async def _concurrently(app, path, count):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await asyncio.gather(*(client.get(path) for _ in range(count)), return_exceptions=True)


@pytest.mark.parametrize("checkout_pause", [0, 0.25])
def test_slow_waits_do_not_exhaust_a_small_pool(tmp_path, checkout_pause):
    # Give finite worker work scheduling room. Connection release is checked
    # directly below, rather than inferred from beating a 200 ms pool timeout.
    engine = _one_connection_pool(tmp_path, pool_timeout=1)
    sessions = sessionmaker(bind=engine)
    if checkout_pause:
        # Reproduce a worker descheduled after it acquires the only slot.
        event.listen(engine, "checkout", lambda *_: time.sleep(checkout_pause), once=True)

    async def scenario():
        arrived = 0
        all_waiting, release = asyncio.Event(), asyncio.Event()

        async def external_wait():
            nonlocal arrived
            arrived += 1
            if arrived == 8:
                all_waiting.set()
            await release.wait()

        requests = asyncio.create_task(_concurrently(_app(sessions, external_wait), "/released", 8))
        try:
            await asyncio.wait_for(all_waiting.wait(), timeout=5)
            # Every request is parked outside its database step. A session
            # retained across that await cannot be hidden by a longer timeout.
            assert engine.pool.checkedout() == 0, engine.pool.status()
        finally:
            release.set()
            replies = await asyncio.wait_for(requests, timeout=5)
            failures = [repr(reply) for reply in replies if isinstance(reply, BaseException)]
            assert not failures, failures
        assert [reply.json() for reply in replies] == [{"ok": True}] * 8
        assert engine.pool.checkedout() == 0

    try:
        with patch("vendoo_studio.database.SessionLocal", sessions):
            asyncio.run(scenario())
    finally:
        engine.dispose()


def test_holding_a_session_across_a_slow_wait_does_exhaust_it(tmp_path):
    """The failure the hosted API saw, reproduced so the test above means something."""
    engine = _one_connection_pool(tmp_path)
    sessions = sessionmaker(bind=engine)
    try:
        with patch("vendoo_studio.database.SessionLocal", sessions):
            replies = asyncio.run(_concurrently(_app(sessions), "/held", 4))
        assert any(isinstance(reply, PoolTimeout) for reply in replies)
    finally:
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


def test_a_claim_cancelled_midway_still_lands_and_is_undone(tmp_path):
    """A dropped request must not leave a job or a ``sending`` mark claimed with nobody settling it."""
    import threading

    engine = _one_connection_pool(tmp_path)
    sessions = sessionmaker(bind=engine)
    started, release, undone = threading.Event(), threading.Event(), []

    def claim(db):
        db.execute(text("SELECT 1"))
        started.set()
        assert release.wait(5)
        return "claimed"

    async def undo(claimed):
        undone.append(claimed)

    async def scenario():
        caller = asyncio.ensure_future(claim_in_worker_session(claim, undo))
        await asyncio.to_thread(started.wait, 5)
        caller.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await caller

    with patch("vendoo_studio.database.SessionLocal", sessions):
        asyncio.run(scenario())
    assert undone == ["claimed"]
    engine.dispose()
