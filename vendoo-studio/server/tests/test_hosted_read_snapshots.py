from concurrent.futures import ThreadPoolExecutor
import asyncio
import threading
from unittest.mock import MagicMock

import pytest

from test_hosted_drafts import cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import jwks, signing_keys  # noqa: F401
from vendoo_studio.services import hosted_read_snapshots as snapshots
from vendoo_studio.services.service_context import ServiceContext, use


def context(owner):
    return ServiceContext(owner, MagicMock(), MagicMock(), MagicMock(), MagicMock())


def test_shared_build_survives_disconnect_and_keeps_sellers_separate(monkeypatch):
    cache = snapshots.SellerReadSnapshots()
    entered, release, checked = threading.Event(), threading.Event(), threading.Event()
    versions, builds = [], []

    def version(owner):
        versions.append(owner.seller_id)
        if len(versions) >= 10:
            checked.set()
        return 1

    def load(owner, read):
        builds.append(owner.seller_id)
        entered.set()
        assert release.wait(5)
        return {'owner': owner.seller_id}

    monkeypatch.setattr(snapshots, '_version', version)
    monkeypatch.setattr(snapshots, '_load', load)

    async def run():
        with use(context('alice')):
            first = asyncio.create_task(cache.read('inventory', lambda db: None))
            assert await asyncio.to_thread(entered.wait, 3)
            peers = [asyncio.create_task(cache.read('inventory', lambda db: None)) for _ in range(9)]
            try:
                assert await asyncio.to_thread(checked.wait, 3)
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
            finally:
                release.set()
            assert await asyncio.gather(*peers) == [{'owner': 'alice'}] * 9
            assert builds == ['alice']
        with use(context('bob')):
            assert await cache.read('inventory', lambda db: None) == {'owner': 'bob'}
        assert builds == ['alice', 'bob']

    asyncio.run(run())


def test_change_during_build_is_not_cached_and_failed_reads_can_retry(monkeypatch):
    from fastapi import HTTPException
    from sqlalchemy.exc import SQLAlchemyError

    cache = snapshots.SellerReadSnapshots()
    versions = [0]
    monkeypatch.setattr(snapshots, '_version', lambda _: versions[0])
    monkeypatch.setattr(snapshots, '_load', lambda owner, read: read(owner))

    def changing(_db):
        versions[0] += 1
        return 'older snapshot'

    def broken(_db):
        raise SQLAlchemyError('private diagnostic')

    async def run():
        with use(context('alice')):
            assert await cache.read('inventory', changing) == 'older snapshot'
            assert not cache.completed
            assert await cache.read('inventory', lambda db: 'fresh') == 'fresh'
            with pytest.raises(HTTPException) as error:
                await cache.read('suggestions', broken)
            assert error.value.status_code == 503
            assert 'private diagnostic' not in error.value.detail
            assert await cache.read('suggestions', lambda db: 'retried') == 'retried'
            assert not cache.pending

    asyncio.run(run())


def test_time_based_suggestions_refresh_without_rebuilding_unchanged_inventory(monkeypatch):
    cache = snapshots.SellerReadSnapshots()
    clock, builds = [0], []
    monkeypatch.setattr(snapshots, '_version', lambda _: 1)
    monkeypatch.setattr(snapshots, 'monotonic', lambda: clock[0])

    def load(owner, read):
        builds.append(True)
        return len(builds)

    monkeypatch.setattr(snapshots, '_load', load)

    async def run():
        with use(context('alice')):
            assert await cache.read('inventory', lambda db: None) == 1
            assert await cache.read('suggestions', lambda db: None, refresh_seconds=60) == 2
            clock[0] = 61
            assert await cache.read('inventory', lambda db: None) == 1
            assert await cache.read('suggestions', lambda db: None, refresh_seconds=60) == 3

    asyncio.run(run())


def test_inventory_refreshes_share_work_until_the_seller_data_changes(cloud, signing_keys, monkeypatch):  # noqa: F811
    from vendoo_studio.services import inventory_analytics

    client, _engine = cloud
    alice = headers(signing_keys)
    identity = client.post('/api/drafts', headers=alice, json={'title': 'Original tee'}).json()['id']
    # Finish one-time archive/settlement work before measuring ordinary reads.
    assert client.get('/api/conversations', headers=alice).status_code == 200
    assert client.get('/api/conversations', headers=alice).status_code == 200
    original = inventory_analytics.read_analytics_inputs
    calls = []

    def read(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(inventory_analytics, 'read_analytics_inputs', read)
    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(executor.map(lambda _: client.get('/api/conversations', headers=alice), range(8)))
    assert [response.status_code for response in responses] == [200] * 8
    assert not calls, 'Unchanged inventory must reuse the already-computed seller snapshot'
    client.app.state.seller_reads.completed.clear()
    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(executor.map(lambda _: client.get('/api/conversations', headers=alice), range(8)))
    assert [response.status_code for response in responses] == [200] * 8
    assert len(calls) == 1, 'Concurrent cold reads must share one inventory build'
    assert client.patch(f'/api/conversations/{identity}', headers=alice, json={'title': 'Edited tee'}).status_code == 200
    fresh = client.get('/api/conversations', headers=alice)
    assert fresh.json()[0]['title'] == 'Edited tee'
    assert len(calls) == 2, 'A committed change must rebuild the snapshot'
