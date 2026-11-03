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
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.models.user_secret import UserSecretReference
from vendoo_studio.services import hosted_sales_scheduler as worker
from vendoo_studio.services import order_recovery
from vendoo_studio.services.hosted_context import for_seller
from vendoo_studio.services.service_context import current, use


def test_sales_worker_records_each_seller_before_ack_and_replays_safely(cloud, monkeypatch):  # noqa: F811
    _client, engine = cloud
    vault = _fake_vault(monkeypatch)
    for owner in (USER_ID, BOB):
        vault[(owner, 'marketplace-accounts')] = json.dumps({'ebay': {'accountId': owner}})
    with Session(engine) as db:
        for owner in (USER_ID, BOB):
            db.add(UserSecretReference(user_id=owner, account='marketplace-accounts', secret_id=owner))
        db.commit()
    assert set(worker.sales_owners(engine)) == {USER_ID, BOB}
    with use(for_seller(USER_ID)), current().session_factory() as db:
        with pytest.raises(ProgrammingError):
            db.execute(text('SELECT * FROM anteroom_private.sales_detection_owners()'))
    event = {'seq': 1, 'source': 'ebay', 'eventType': 'ORDER_CONFIRMATION',
             'ids': {'orderId': ['same-order'], 'orderLineItemId': ['line-1'], 'listingId': ['123456789012']}}
    acks, catchups = [], []

    async def relay(method, path, **kwargs):
        owner = current().seller_id
        if path == '/studio/ack':
            with current().session_factory() as db:
                assert db.query(MarketplaceSale).count() == 1
            acks.append((owner, kwargs['json_body']['through']))
        return httpx.Response(200, json={'events': [event]}, request=httpx.Request(method, 'https://relay.test'+path))

    async def catchup():
        catchups.append(current().seller_id)

    monkeypatch.setattr(worker.relay_client, 'relay_request', relay)
    monkeypatch.setattr(worker.recent_ebay_orders, 'refresh', catchup)
    monkeypatch.setattr(order_recovery.EbayClient, 'connect', AsyncMock(
        side_effect=lambda: ebay_notification_client('same-order', [('line-1', '123456789012')])))
    monkeypatch.setattr(order_recovery, 'assert_identity', lambda _: None)

    async def run():
        for _ in range(2):
            for owner in (USER_ID, BOB):
                await worker.check_owner(owner)
    asyncio.run(run())
    assert acks == [(USER_ID, 1), (BOB, 1)] * 2
    assert catchups == [USER_ID, BOB] * 2
    for owner in (USER_ID, BOB):
        with use(for_seller(owner)), current().session_factory() as db:
            assert db.query(MarketplaceSale).one().order_id == 'same-order'


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
    monkeypatch.setattr(worker.recent_ebay_orders, 'refresh', catchup)
    asyncio.run(worker.check_owner(USER_ID))
    assert '/studio/ack' not in calls
    assert 'failed-event' in calls
    assert 'catchup' in calls


def test_timer_checks_immediately_and_one_seller_failure_does_not_stop_another(monkeypatch):
    checked = []
    monkeypatch.setattr(worker, 'sales_owners', lambda engine: ['first', 'second'])

    async def check(owner):
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
