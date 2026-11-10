"""Backlog reads must not block the request loop or lose seller context."""
import asyncio
import threading
from contextlib import contextmanager
from contextvars import ContextVar

import pytest

from vendoo_studio.services import order_amounts


@pytest.mark.parametrize("fails", [False, True])
def test_backfill_reads_and_closes_session_off_loop(monkeypatch, fails):
    owner = ContextVar("backfill_test_owner", default=None)
    events = []
    session = object()
    loop_thread = threading.get_ident()

    def note(phase):
        events.append((phase, threading.get_ident(), owner.get()))

    @contextmanager
    def factory():
        note("open")
        try:
            yield session
        finally:
            note("close")

    def backlog(db, marketplace, limit):
        assert db is session
        assert (marketplace, limit) == ("etsy", 2)
        note("query")
        if fails:
            raise RuntimeError("database unavailable")
        return []

    monkeypatch.setattr(order_amounts, "_backlog", backlog)
    token = owner.set("seller-a")
    try:
        if fails:
            with pytest.raises(RuntimeError, match="database unavailable"):
                asyncio.run(order_amounts.backfill("etsy", factory, limit=2))
        else:
            assert asyncio.run(order_amounts.backfill("etsy", factory, limit=2)) == 0
    finally:
        owner.reset(token)
    assert [phase for phase, _, _ in events] == ["open", "query", "close"]
    assert all(thread != loop_thread for _, thread, _ in events)
    assert len({thread for _, thread, _ in events}) == 1
    assert all(seller == "seller-a" for _, _, seller in events)
