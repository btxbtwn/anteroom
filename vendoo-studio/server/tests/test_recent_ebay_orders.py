import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from vendoo_studio.services import recent_ebay_orders as sync


@pytest.fixture
def setup_sync(monkeypatch):
    sync._due.clear()
    sync._running.clear()
    clock = [1000]
    monkeypatch.setattr(sync.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(sync, "sale_detection_enabled", lambda _name: True)
    monkeypatch.setattr("vendoo_studio.services.keychain.get_marketplace_accounts",
                        lambda: {"ebay": {"accountId": "seller", "connectedAt": 123}})
    factory = MagicMock()
    monkeypatch.setattr(sync, "current", lambda: SimpleNamespace(seller_id="alice", session_factory=factory))
    recover = AsyncMock(return_value={"nextPage": None})
    monkeypatch.setattr(sync.order_recovery, "recover", recover)
    return recover, factory, clock


def test_catch_up_reuses_read_only_recovery_and_throttles(setup_sync):
    recover, factory, _clock = setup_sync
    asyncio.run(sync.refresh())
    asyncio.run(sync.refresh())
    assert recover.await_count == 1
    args = recover.call_args.args
    assert args[0] is factory.return_value.__enter__.return_value
    assert args[1] == "ebay"
    assert (args[3] - args[2]).days == 6
    assert args[4:] == (1, "123", None, None)


def test_catch_up_walks_pages_with_fixed_connection_and_boundary(setup_sync):
    recover, _factory, _clock = setup_sync
    through = datetime.now(UTC).isoformat()
    recover.side_effect = [{"nextPage": 2, "connection": "fingerprint", "through": through}, {"nextPage": None}]
    asyncio.run(sync.refresh())
    assert recover.await_count == 2
    assert recover.call_args.args[4:] == (2, "123", "fingerprint", datetime.fromisoformat(through))


def test_failed_catch_up_retries_after_interval(setup_sync):
    recover, _factory, clock = setup_sync
    recover.side_effect = RuntimeError("private provider response")
    asyncio.run(sync.refresh())
    asyncio.run(sync.refresh())
    assert not sync._running
    assert recover.await_count == 1
    clock[0] += sync.INTERVAL_S
    recover.side_effect = None
    asyncio.run(sync.refresh())
    assert recover.await_count == 2


def test_disabled_detection_makes_no_remote_read(setup_sync, monkeypatch):
    recover, _factory, _clock = setup_sync
    monkeypatch.setattr(sync, "sale_detection_enabled", lambda _name: False)
    asyncio.run(sync.refresh())
    recover.assert_not_awaited()


def test_sellers_have_independent_catch_up_limits(setup_sync, monkeypatch):
    recover, factory, _clock = setup_sync
    asyncio.run(sync.refresh())
    monkeypatch.setattr(sync, "current", lambda: SimpleNamespace(seller_id="bob", session_factory=factory))
    asyncio.run(sync.refresh())
    assert recover.await_count == 2


def test_overlapping_refreshes_do_not_duplicate_reads(setup_sync):
    recover, _factory, _clock = setup_sync

    async def check():
        entered, release = asyncio.Event(), asyncio.Event()

        async def read(*_args):
            entered.set()
            await release.wait()
            return {"nextPage": None}

        recover.side_effect = read
        first = asyncio.create_task(sync.refresh())
        await entered.wait()
        await sync.refresh()
        release.set()
        await first

    asyncio.run(check())
    assert recover.await_count == 1


def test_sale_poll_runs_catch_up_after_response(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from vendoo_studio.database import get_db
    from vendoo_studio.routes.marketplace_accounts import router

    app = FastAPI()
    app.include_router(router)
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
    app.dependency_overrides[get_db] = lambda: db
    refresh = AsyncMock()
    monkeypatch.setattr(sync, "refresh", refresh)
    with TestClient(app) as client:
        assert client.get("/api/marketplace-sales").status_code == 200
    refresh.assert_awaited_once()
