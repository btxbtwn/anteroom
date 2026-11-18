from concurrent.futures import ThreadPoolExecutor
import asyncio
import threading
from unittest.mock import MagicMock

import pytest

from test_hosted_drafts import cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
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


def test_busy_membership_changes_the_version_but_lease_renewals_do_not(cloud, signing_keys):  # noqa: F811
    from datetime import UTC, datetime, timedelta
    from sqlalchemy.orm import Session

    from vendoo_studio.models.background_task import BackgroundTask
    from vendoo_studio.services.hosted_context import for_seller

    client, engine = cloud
    alice = headers(signing_keys)
    identity = client.post('/api/drafts', headers=alice, json={'title': 'Busy tee'}).json()['id']
    owner = for_seller(USER_ID)
    idle = snapshots._version(owner)
    # The trusted worker also uses SQL outside SellerDatabaseSession. Cache
    # correctness cannot depend exclusively on ORM feed notifications.
    with Session(engine) as db:
        task = BackgroundTask(user_id=USER_ID, conversation_id=identity, kind='chat', payload={})
        db.add(task)
        db.commit()
        task_id = task.id
    busy = snapshots._version(owner)
    assert busy != idle
    with Session(engine) as db:
        task = db.get(BackgroundTask, task_id)
        task.status = 'running'
        task.leased_until = datetime.now(UTC) + timedelta(minutes=2)
        db.commit()
    assert snapshots._version(owner) == busy
    with Session(engine) as db:
        db.get(BackgroundTask, task_id).status = 'cancelled'
        db.commit()
    assert snapshots._version(owner) == idle


def timed(read):
    """Run one snapshot read the way a request does; return its slow-line fields."""
    from vendoo_studio.services import request_timing

    async def run():
        fields = {}
        token = request_timing._details.set(fields)
        try:
            return await read(), fields
        finally:
            request_timing._details.reset(token)
    return run()


def test_slow_line_separates_waiting_on_a_discarded_build_from_rebuilding(monkeypatch):
    # Fable's unmeasured hypothesis: a build whose own commit moves the change
    # vector is discarded, so a tab that waited on it builds a second time.
    cache = snapshots.SellerReadSnapshots()
    versions, checks, entered, release = [0], [], threading.Event(), threading.Event()

    def version(_owner):
        checks.append(True)
        return versions[0]

    def load(owner, read):
        if read == 'committing':
            entered.set()
            assert release.wait(5)
            versions[0] += 1
        return ['row'] * 11

    monkeypatch.setattr(snapshots, '_version', version)
    monkeypatch.setattr(snapshots, '_load', load)

    async def run():
        with use(context('alice')):
            first = asyncio.create_task(timed(lambda: cache.read('inventory', 'committing')))
            assert await asyncio.to_thread(entered.wait, 3)
            second = asyncio.create_task(timed(lambda: cache.read('inventory', 'plain')))
            while len(checks) < 2:
                await asyncio.sleep(0.001)
            # The second tab is now waiting on the first build.
            await asyncio.sleep(0.05)
            release.set()
            (_rows, built), (_again, waited) = await first, await second
            (_cached, hit) = await timed(lambda: cache.read('inventory', 'plain'))
        return built, waited, hit

    built, waited, hit = asyncio.run(run())
    assert {key: built[key] for key in ('checks', 'waits', 'stale_peers', 'builds', 'uncached', 'rows_le')} == {
        'checks': 1, 'waits': 0, 'stale_peers': 0, 'builds': 1, 'uncached': 1, 'rows_le': 100}
    assert {key: waited[key] for key in ('checks', 'waits', 'stale_peers', 'builds', 'uncached')} == {
        'checks': 2, 'waits': 1, 'stale_peers': 1, 'builds': 1, 'uncached': 0}
    assert waited['wait_ms'] >= 40
    assert {key: hit[key] for key in ('checks', 'waits', 'builds', 'build_total_ms', 'rows_le')} == {
        'checks': 1, 'waits': 0, 'builds': 0, 'build_total_ms': 0, 'rows_le': 0}
    # Same field names whatever happened, all numeric except the fixed read
    # name and pool use: alert grouping strips digits, so one route stays one
    # group and no seller or listing detail reaches the line.
    assert list(built) == list(waited) == list(hit)
    for fields in (built, waited, hit):
        assert fields['snapshot'] == 'inventory'
        assert all(isinstance(value, int) for key, value in fields.items()
                   if key not in ('snapshot', 'pool_start', 'pool_end'))
        assert 'alice' not in repr(fields)


def test_load_splits_connection_database_and_assembly_time_and_counts_commits(monkeypatch):
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    engine = create_engine('sqlite://')
    clock = [0.0]
    monkeypatch.setattr(snapshots, 'perf_counter', lambda: clock[0])
    owner = ServiceContext('alice', lambda: Session(engine), MagicMock(), MagicMock(), MagicMock())

    def read(db):
        clock[0] += 2  # status corrections, then a commit
        db.execute(text('select 1'))
        db.commit()
        clock[0] += 3  # remaining reads, then the connection is released
        db.execute(text('select 1'))
        db.close()
        clock[0] += 4  # response assembly without a connection
        return ['row']

    phases = {}
    token = snapshots._build_phases.set(phases)
    try:
        assert snapshots._load(owner, read) == ['row']
    finally:
        snapshots._build_phases.reset(token)
    assert phases == {'commits': 1, 'connect': 0.0, 'db': 5.0, 'assemble': 4.0}


def test_inventory_slow_line_reports_a_real_seller_build(cloud, signing_keys, monkeypatch, caplog):  # noqa: F811
    import logging
    import re

    from sqlalchemy.orm import Session

    from vendoo_studio import database
    from vendoo_studio.models.conversation import Conversation
    from vendoo_studio.services import request_timing
    from vendoo_studio.services.hosted_database import SellerDatabaseSession

    client, engine = cloud
    alice = headers(signing_keys)
    identity = client.post('/api/drafts', headers=alice, json={'title': 'Phase tee'}).json()['id']
    # Finish one-time archive/settlement work before measuring ordinary reads.
    assert client.get('/api/conversations', headers=alice).status_code == 200
    assert client.get('/api/conversations', headers=alice).status_code == 200
    sessions, original = [], snapshots._watch

    def watch(db, phases):
        sessions.append((type(db), db.get_bind() is database.engine))
        original(db, phases)

    monkeypatch.setattr(snapshots, '_watch', watch)
    monkeypatch.setattr(request_timing, 'SLOW_SECONDS', 0)
    # A leftover busy label with nothing generating: the inventory build's
    # status reconcile corrects it and commits. Written outside the seller
    # session, so no feed row; drop the cached snapshot to force a build.
    with Session(engine) as db:
        db.get(Conversation, identity).status = 'in_progress'
        db.commit()
    client.app.state.seller_reads.completed.clear()

    def fields():
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=request_timing.__name__):
            assert client.get('/api/conversations', headers=alice).status_code == 200
        [line] = [record.getMessage() for record in caplog.records
                  if record.name == request_timing.__name__ and ' /api/conversations ' in record.getMessage()]
        assert identity not in line and USER_ID not in line
        return dict(field.split('=') for field in line.split()[6:])

    corrected, rebuilt, hit = fields(), fields(), fields()
    # The real build ran on a seller session bound to the engine whose pool
    # the line reports.
    assert sessions == [(SellerDatabaseSession, True), (SellerDatabaseSession, True)]
    # Tests run on NullPool; hosted runs a DiagnosticQueuePool (see the next test).
    size = getattr(engine.pool, 'size', None)
    pool = rf'\d+/{size()}' if callable(size) else '0/0'
    for line in (corrected, rebuilt, hit):
        assert line['snapshot'] == 'inventory'
        assert re.fullmatch(pool, line['pool_start']) and re.fullmatch(pool, line['pool_end'])
    # The correction's commit moved the change vector, so that build was not
    # cached and the next read built again without committing.
    assert (corrected['builds'], corrected['uncached'], corrected['rows_le']) == ('1', '1', '10')
    assert int(corrected['build_commits']) >= 1
    assert (rebuilt['builds'], rebuilt['uncached'], rebuilt['build_commits']) == ('1', '0', '0')
    assert (hit['builds'], hit['waits'], hit['build_total_ms']) == ('0', '0', '0')
    for step in snapshots.INVENTORY_STEPS:
        assert int(rebuilt[f'inv_{step}_ms']) >= 0
        assert int(rebuilt[f'inv_{step}_orm_calls']) >= 0
        assert hit[f'inv_{step}_ms'] == hit[f'inv_{step}_orm_calls'] == '0'
    assert int(rebuilt['inv_conversations_orm_calls']) == 1
    assert int(rebuilt['inv_covers_orm_calls']) == 1
    assert rebuilt['inv_assemble_orm_calls'] == '0'
    for line in (corrected, rebuilt):
        phases = [int(line[f'build_{phase}_ms']) for phase in ('connect', 'db', 'assemble', 'recheck')]
        assert all(value >= 0 for value in phases)
        assert sum(phases) <= int(line['build_total_ms']) + len(phases)  # rounding


def test_pool_use_reads_the_hosted_queue_pool_without_a_query(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    from vendoo_studio.database_pool import DiagnosticQueuePool

    hosted = create_engine('sqlite://', poolclass=DiagnosticQueuePool, pool_size=3, max_overflow=2)
    monkeypatch.setattr('vendoo_studio.database.engine', hosted)
    assert snapshots._pool_use() == '0/3'
    with hosted.connect():
        assert snapshots._pool_use() == '1/3'
    assert snapshots._pool_use() == '0/3'
    monkeypatch.setattr('vendoo_studio.database.engine', create_engine('sqlite://', poolclass=NullPool))
    assert snapshots._pool_use() == '0/0'


def test_inventory_step_counts_are_parameter_free_and_removed_after_failure(monkeypatch):
    from sqlalchemy import create_engine, select, literal
    from sqlalchemy.orm import Session

    phases = {}
    token = snapshots._build_phases.set(phases)
    clock = iter((1.0, 1.25, 2.0, 2.5))
    monkeypatch.setattr(snapshots, 'perf_counter', lambda: next(clock))
    engine = create_engine('sqlite://')
    try:
        with Session(engine) as db:
            with snapshots.inventory_step(db, 'conversations'):
                assert db.scalar(select(literal('private seller title'))) == 'private seller title'
            with pytest.raises(RuntimeError, match='private failure'):
                with snapshots.inventory_step(db, 'covers'):
                    db.scalar(select(literal('private photo id')))
                    raise RuntimeError('private failure')
            db.scalar(select(literal('later private value')))
        assert phases == {'step_conversations_ms': 250, 'step_conversations_orm_calls': 1,
                          'step_covers_ms': 500, 'step_covers_orm_calls': 1}
        assert 'private' not in repr(phases)
    finally:
        snapshots._build_phases.reset(token)
        engine.dispose()


def test_inventory_step_is_inert_outside_a_snapshot_build():
    with snapshots.inventory_step(None, 'analytics'):
        pass
    phases = {}
    token = snapshots._build_phases.set(phases)
    try:
        with snapshots.inventory_step(None, 'private dynamic name'):
            pass
        assert phases == {}
    finally:
        snapshots._build_phases.reset(token)


def test_step_diagnostics_follow_shared_build_and_reset_on_cache_hit(monkeypatch):
    from dataclasses import replace
    from sqlalchemy import create_engine, literal, select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool

    engine = create_engine('sqlite://', poolclass=NullPool)

    owner = replace(context('private seller identity'), session_factory=sessionmaker(engine))
    cache, reports = snapshots.SellerReadSnapshots(), []
    monkeypatch.setattr(snapshots, '_version', lambda _: 1)
    monkeypatch.setattr(snapshots, 'note', lambda **fields: reports.append(fields))

    def read(db):
        with snapshots.inventory_step(db, 'listings'):
            return db.scalar(select(literal('private listing contents')))

    async def run():
        with use(owner):
            assert await cache.read('inventory', read) == 'private listing contents'
            assert await cache.read('inventory', read) == 'private listing contents'

    try:
        asyncio.run(run())
        built, hit = reports
        assert built['inv_listings_orm_calls'] == 1
        assert built['inv_covers_orm_calls'] == 0
        assert all(value == 0 for key, value in hit.items() if key.startswith('inv_'))
        assert 'private' not in repr(reports)
    finally:
        engine.dispose()


def test_inventory_step_parallel_sessions_do_not_mix_diagnostics():
    from sqlalchemy import create_engine, literal, select
    from sqlalchemy.orm import Session

    def run(calls):
        engine = create_engine('sqlite://')
        phases = {}
        token = snapshots._build_phases.set(phases)
        try:
            with Session(engine) as db, snapshots.inventory_step(db, 'analytics'):
                for _ in range(calls):
                    db.scalar(select(literal('private parameter')))
            return phases['step_analytics_orm_calls']
        finally:
            snapshots._build_phases.reset(token)
            engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert list(workers.map(run, (1, 3))) == [1, 3]
