import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from vendoo_studio.services import recent_orders as sync


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
    monkeypatch.setattr(sync.order_amounts, "backfill", AsyncMock(return_value=0))
    return recover, factory, clock


def test_catch_up_reuses_read_only_recovery_and_throttles(setup_sync):
    recover, factory, _clock = setup_sync
    asyncio.run(sync.refresh())
    asyncio.run(sync.refresh())
    assert recover.await_count == 1
    args = recover.call_args.args
    assert recover.call_args.kwargs == {"session_factory": factory}
    assert args[0] == "ebay"
    assert (args[2] - args[1]).days == 6
    assert args[3:] == (1, "123", None, None)


def test_catch_up_walks_pages_with_fixed_connection_and_boundary(setup_sync):
    recover, _factory, _clock = setup_sync
    through = datetime.now(UTC).isoformat()
    recover.side_effect = [{"nextPage": 2, "connection": "fingerprint", "through": through}, {"nextPage": None}]
    asyncio.run(sync.refresh())
    assert recover.await_count == 2
    assert recover.call_args.args[3:] == (2, "123", "fingerprint", datetime.fromisoformat(through))


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


def test_etsy_orders_are_caught_up_with_the_same_read_only_recovery(setup_sync, monkeypatch):
    recover, _factory, _clock = setup_sync
    monkeypatch.setattr("vendoo_studio.services.keychain.get_marketplace_accounts",
                        lambda: {"etsy": {"accountId": "shop", "connectedAt": 456}})
    asyncio.run(sync.refresh())
    assert recover.await_count == 1
    args = recover.call_args.args
    assert args[0] == "etsy"
    assert (args[2] - args[1]).days == 6
    assert args[3:] == (1, "456", None, None)


def test_failed_ebay_catch_up_does_not_stop_etsy(setup_sync, monkeypatch):
    recover, _factory, _clock = setup_sync
    monkeypatch.setattr("vendoo_studio.services.keychain.get_marketplace_accounts",
                        lambda: {"ebay": {"accountId": "seller", "connectedAt": 123},
                                 "etsy": {"accountId": "shop", "connectedAt": 456}})

    async def read(_db, marketplace, *_args):
        if marketplace == "ebay":
            raise RuntimeError("private provider response")
        return {"nextPage": None}

    recover.side_effect = read
    asyncio.run(sync.refresh())
    assert [call.args[0] for call in recover.call_args_list] == ["ebay", "etsy"]


def test_marketplaces_have_independent_throttles(setup_sync, monkeypatch):
    recover, _factory, _clock = setup_sync
    asyncio.run(sync.refresh())
    monkeypatch.setattr("vendoo_studio.services.keychain.get_marketplace_accounts",
                        lambda: {"ebay": {"accountId": "seller", "connectedAt": 123},
                                 "etsy": {"accountId": "shop", "connectedAt": 456}})
    asyncio.run(sync.refresh())
    assert [call.args[0] for call in recover.call_args_list] == ["ebay", "etsy"]


def test_disabled_etsy_detection_makes_no_etsy_read(setup_sync, monkeypatch):
    recover, _factory, _clock = setup_sync
    monkeypatch.setattr(sync, "sale_detection_enabled", lambda name: name != "etsy")
    monkeypatch.setattr("vendoo_studio.services.keychain.get_marketplace_accounts",
                        lambda: {"ebay": {"accountId": "seller", "connectedAt": 123},
                                 "etsy": {"accountId": "shop", "connectedAt": 456}})
    asyncio.run(sync.refresh())
    assert [call.args[0] for call in recover.call_args_list] == ["ebay"]


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

        async def read(*_args, **_kwargs):
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


def test_each_catch_up_also_fills_older_sale_amounts_even_after_a_failed_read(setup_sync):
    recover, factory, _clock = setup_sync
    recover.side_effect = RuntimeError("private provider response")
    asyncio.run(sync.refresh())
    sync.order_amounts.backfill.assert_awaited_once_with("ebay", factory)
