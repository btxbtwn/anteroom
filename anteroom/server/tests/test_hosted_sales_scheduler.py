"""The hosted sales worker records each seller's events without an open browser."""
import asyncio
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud  # noqa: F401
from test_hosted_marketplaces import _fake_vault
from test_relay_sales import ebay_notification_client
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from anteroom.models.marketplace_sale import MarketplaceSale
from anteroom.models.user_secret import UserSecretReference
from anteroom.services import hosted_sales_scheduler as worker
from anteroom.services import order_recovery
from anteroom.services.hosted_context import for_seller
from anteroom.services.service_context import current, use


def test_sales_worker_records_each_seller_before_ack_and_replays_safely(cloud, monkeypatch):  # noqa: F811
    _client, engine = cloud
    vault = _fake_vault(monkeypatch)
    for owner in (USER_ID, BOB):
        vault[(owner, 'marketplace-accounts')] = json.dumps({'ebay': {'accountId': owner}, 'etsy': {'accountId': 'shop'}})
    with Session(engine) as db:
        for owner in (USER_ID, BOB):
            db.add(UserSecretReference(user_id=owner, account='marketplace-accounts', secret_id=owner))
        db.commit()
    assert set(worker.sales_owners(engine)) == {USER_ID, BOB}
    with use(for_seller(USER_ID)), current().session_factory() as db:
        with pytest.raises(ProgrammingError):
            db.execute(text('SELECT * FROM anteroom_private.sales_detection_owners()'))
    events = [
        {'seq': 1, 'source': 'ebay', 'eventType': 'ORDER_CONFIRMATION',
         'ids': {'orderId': ['same-order'], 'orderLineItemId': ['line-1'], 'listingId': ['123456789012']}},
        {'seq': 2, 'source': 'etsy', 'eventType': 'order.paid', 'ids': {'shop_id': ['shop'], 'receipt_id': ['receipt']}},
    ]
    acks, catchups, announcements = [], [], []

    async def relay(method, path, **kwargs):
        owner = current().seller_id
        if path == '/studio/ack':
            with current().session_factory() as db:
                assert db.query(MarketplaceSale).count() == 2
            acks.append((owner, kwargs['json_body']['through']))
        return httpx.Response(200, json={'events': events}, request=httpx.Request(method, 'https://relay.test'+path))

    async def catchup():
        catchups.append(current().seller_id)

    async def announce(sale_ids, *, session_factory):
        assert session_factory is current().session_factory
        with session_factory() as db:
            saved_ids = {sale.id for sale in db.query(MarketplaceSale).all()}
        assert len(sale_ids) == 2
        assert set(sale_ids) == saved_ids
        announcements.append(current().seller_id)

    monkeypatch.setattr(worker.relay_client, 'relay_request', relay)
    monkeypatch.setattr(worker.recent_orders, 'refresh', catchup)
    monkeypatch.setattr(worker.marketplace_accounts, 'announce_sales', announce)
    monkeypatch.setattr(worker.marketplace_accounts, 'read_etsy_receipt', AsyncMock(
        return_value={'transactions': [{'transaction_id': 1, 'listing_id': 123, 'quantity': 1}]}))
    monkeypatch.setattr(order_recovery.EbayClient, 'connect', AsyncMock(
        side_effect=lambda: ebay_notification_client('same-order', [('line-1', '123456789012')])))
    monkeypatch.setattr(order_recovery, 'assert_identity', lambda _: None)

    async def run():
        for _ in range(2):
            for owner in (USER_ID, BOB):
                await worker.check_owner(owner, {})
    asyncio.run(run())
    assert acks == [(USER_ID, 2), (BOB, 2)] * 2
    assert catchups == [USER_ID, BOB] * 2
    assert announcements == [USER_ID, BOB]
    for owner in (USER_ID, BOB):
        with use(for_seller(owner)), current().session_factory() as db:
            assert {sale.order_id for sale in db.query(MarketplaceSale).all()} == {'same-order', 'receipt'}


def test_failed_event_is_not_acknowledged_and_catchup_still_runs(monkeypatch):
    calls = []

    async def relay(method, path, **kwargs):
        calls.append(path)
        return httpx.Response(200, json={'events': [{'seq': 1}]}, request=httpx.Request(method, 'https://relay.test'+path))

    async def fail(*args, **kwargs):
        calls.append('failed-event')
        raise RuntimeError('event did not commit')

    async def catchup():
        calls.append('catchup')

    monkeypatch.setattr(worker, 'for_seller', lambda owner: SimpleNamespace(session_factory=lambda: None))
    monkeypatch.setattr(worker, 'use', lambda context: nullcontext())
    monkeypatch.setattr(worker, 'get_marketplace_accounts', lambda: {'ebay': {}})
    monkeypatch.setattr(worker.relay_client, 'relay_request', relay)
    monkeypatch.setattr(worker.relay_sales, 'handle_event', fail)
    monkeypatch.setattr(worker.recent_orders, 'refresh', catchup)
    asyncio.run(worker.check_owner(USER_ID, {}))
    assert '/studio/ack' not in calls
    assert 'failed-event' in calls
    assert 'catchup' in calls


def test_inbox_advances_after_three_failures_of_the_same_event(monkeypatch, caplog):
    attempts, recorded, acks = [], [], []
    poll, acknowledged = 0, 0

    async def relay(method, path, **kwargs):
        nonlocal acknowledged
        status = 200
        body = {}
        if path == '/studio/events':
            body = {'events': [{'seq': seq} for seq in (1, 2) if seq > acknowledged]}
        else:
            through = kwargs['json_body']['through']
            acks.append((poll, through))
            if poll == 2:
                status = 503
            else:
                acknowledged = through
        return httpx.Response(status, json=body, request=httpx.Request(method, 'https://relay.test'+path))

    async def handle(event, **kwargs):
        seq = event['seq']
        attempts.append((poll, seq))
        if seq == 1:
            raise RuntimeError('event did not commit')
        if seq not in recorded:
            recorded.append(seq)
        return []

    async def next_poll(seconds):
        nonlocal poll
        poll += 1
        if poll == 4:
            raise asyncio.CancelledError

    monkeypatch.setattr(worker, 'sales_owners', lambda engine: [USER_ID])
    monkeypatch.setattr(worker, 'get_marketplace_accounts', lambda: {'ebay': {}})
    monkeypatch.setattr(worker.sale_delist_followup, 'start', lambda: None)
    monkeypatch.setattr(worker.relay_client, 'relay_request', relay)
    monkeypatch.setattr(worker.relay_sales, 'handle_event', handle)
    monkeypatch.setattr(worker.recent_orders, 'refresh', AsyncMock())
    monkeypatch.setattr(worker.asyncio, 'sleep', next_poll)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker.serve(None))

    assert attempts == [(0, 1), (1, 1), (2, 1), (2, 2), (3, 2)]
    assert recorded == [2]
    assert acks == [(2, 2), (3, 2)]
    assert len([record for record in caplog.records if record.levelname == 'ERROR']) == 1


def test_seller_can_finish_while_another_waits_for_the_relay(monkeypatch):
    monkeypatch.setattr(worker, 'sales_owners', lambda engine: [USER_ID, BOB])
    monkeypatch.setattr(worker, 'get_marketplace_accounts', lambda: {'ebay': {}})
    monkeypatch.setattr(worker.sale_delist_followup, 'start', lambda: None)

    async def run():
        waiting, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def relay(method, path, **kwargs):
            if current().seller_id == USER_ID:
                waiting.set()
                await release.wait()
            return httpx.Response(200, json={'events': []}, request=httpx.Request(method, 'https://relay.test'+path))

        async def catchup():
            if current().seller_id == BOB:
                finished.set()

        monkeypatch.setattr(worker.relay_client, 'relay_request', relay)
        monkeypatch.setattr(worker.recent_orders, 'refresh', catchup)
        task = asyncio.create_task(worker.serve(None))
        try:
            await asyncio.wait_for(waiting.wait(), timeout=2)
            await asyncio.wait_for(finished.wait(), timeout=2)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())


def test_timer_checks_immediately_and_one_seller_failure_does_not_stop_another(monkeypatch):
    checked = []
    monkeypatch.setattr(worker, 'sales_owners', lambda engine: ['first', 'second'])

    async def check(owner, event_failures):
        checked.append(owner)
        if owner == 'first':
            raise RuntimeError('unavailable')

    async def stop(seconds):
        assert seconds == worker.INTERVAL_S
        raise asyncio.CancelledError

    monkeypatch.setattr(worker, 'check_owner', check)
    monkeypatch.setattr(worker.asyncio, 'sleep', stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker.serve(None))
    assert checked == ['first', 'second']
