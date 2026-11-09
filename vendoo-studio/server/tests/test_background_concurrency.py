import asyncio
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from vendoo_studio.services import background_worker
from vendoo_studio.services.background_loop import BackgroundLoop


@pytest.fixture
def heartbeat_loop():
    loop = BackgroundLoop('test-draft-leases')
    yield loop
    loop.stop()
    assert not loop.thread.is_alive()


def test_worker_starts_independent_jobs_before_previous_jobs_finish_and_drains(monkeypatch):
    async def run():
        stopping = threading.Event()
        jobs = iter([('draft-a', 'alice', 'lease-a'), ('draft-b', 'bob', 'lease-b'),
                     ('draft-c', 'alice', 'lease-c')])
        started = []
        all_started, release = asyncio.Event(), asyncio.Event()

        async def execute(*job, **_kwargs):
            started.append(job)
            if len(started) == 3:
                stopping.set()
                all_started.set()
            await release.wait()

        monkeypatch.setattr(background_worker.queue, 'claim', lambda _engine: next(jobs, None))
        monkeypatch.setattr(background_worker, 'run_claimed', execute)
        worker = asyncio.create_task(background_worker.serve(None, stopping))
        try:
            await asyncio.wait_for(all_started.wait(), 2)
            assert [job[1] for job in started] == ['alice', 'bob', 'alice']
            assert not worker.done(), 'Shutdown must drain every active draft'
            release.set()
            await asyncio.wait_for(worker, 2)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    asyncio.run(run())


def test_worker_failure_logs_location_without_private_exception_text(monkeypatch, caplog, heartbeat_loop):
    finished = []

    async def fail(*_args):
        raise RuntimeError('private database credential')

    def finish(*args, **kwargs):
        finished.append((args, kwargs))

    monkeypatch.setattr(background_worker, '_execute', fail)
    monkeypatch.setattr(background_worker.queue, 'finish', finish)
    asyncio.run(background_worker.run_claimed('test-task', str(uuid.uuid4()), 'test-lease',
                                             heartbeat_loop=heartbeat_loop))
    assert len(finished) == 1 and finished[0][1]['error']
    assert 'RuntimeError' in caplog.text
    assert 'test_background_concurrency.py' in caplog.text
    assert 'private database credential' not in caplog.text


def test_seller_stop_ends_the_task_without_reporting_a_failure(monkeypatch, caplog, heartbeat_loop):
    finished, sleep = [], asyncio.sleep

    async def quick_heartbeat(seconds):
        await sleep(.01 if seconds == 20 else seconds)

    async def wait_for_provider(*_args):
        await asyncio.Event().wait()

    def stopped(*_args):
        raise background_worker.queue.TaskStopped()

    monkeypatch.setattr(background_worker.asyncio, 'sleep', quick_heartbeat)
    monkeypatch.setattr(background_worker, '_execute', wait_for_provider)
    monkeypatch.setattr(background_worker.queue, 'renew', stopped)
    monkeypatch.setattr(background_worker.queue, 'finish', lambda *args, **kwargs: finished.append(args))
    caplog.set_level('INFO', logger=background_worker.__name__)
    asyncio.run(asyncio.wait_for(background_worker.run_claimed('task', str(uuid.uuid4()), 'lease',
                                                               heartbeat_loop=heartbeat_loop), 2))
    assert not finished, 'A stopped task already settled; the worker must not finish it again'
    assert not [record for record in caplog.records if record.levelname == 'ERROR']
    assert 'stopped by the seller' in caplog.text


def test_lease_renewal_survives_blocking_draft_work_and_preserves_seller_context(monkeypatch, heartbeat_loop):
    from vendoo_studio.services.service_context import current

    blocked, release, renewed = threading.Event(), threading.Event(), threading.Event()
    owners = {'draft-a': str(uuid.uuid4()), 'draft-b': str(uuid.uuid4())}
    seen = []
    started = []
    sleep = asyncio.sleep

    async def quick_heartbeat(seconds):
        await sleep(.01 if seconds == 20 else seconds)

    async def execute(task_id, _token):
        started.append(task_id)
        if len(started) == 2:
            blocked.set()
            release.wait(timeout=5)
        else:
            await asyncio.to_thread(release.wait)
        return False

    def renew(task_id, token):
        seen.append((task_id, token, current().seller_id))
        if {row[0] for row in seen} == set(owners):
            renewed.set()

    async def run():
        await asyncio.gather(*(background_worker.run_claimed(identity, owner, identity + '-lease',
                                                            heartbeat_loop=heartbeat_loop)
                               for identity, owner in owners.items()))

    monkeypatch.setattr(background_worker.asyncio, 'sleep', quick_heartbeat)
    monkeypatch.setattr(background_worker, '_execute', execute)
    monkeypatch.setattr(background_worker.queue, 'renew', renew)
    monkeypatch.setattr(background_worker.queue, 'finish', lambda *_args, **_kwargs: None)
    with ThreadPoolExecutor(max_workers=1) as executor:
        work = executor.submit(asyncio.run, run())
        try:
            assert blocked.wait(2)
            assert renewed.wait(2), 'A synchronous draft query must not expire its own worker lease'
            assert not work.done()
        finally:
            release.set()
            work.result(timeout=2)
    assert seen and all(token == identity + '-lease' and owner == owners[identity]
                        for identity, token, owner in seen)


def test_lease_renewal_continues_until_completion_is_persisted(monkeypatch, heartbeat_loop):
    finishing, release, renewed = threading.Event(), threading.Event(), threading.Event()
    sleep = asyncio.sleep

    async def quick_heartbeat(seconds):
        await sleep(.01 if seconds == 20 else seconds)

    async def execute(*_args):
        return False

    def finish(*_args, **_kwargs):
        finishing.set()
        release.wait(timeout=5)

    monkeypatch.setattr(background_worker.asyncio, 'sleep', quick_heartbeat)
    monkeypatch.setattr(background_worker, '_execute', execute)
    monkeypatch.setattr(background_worker.queue, 'renew', lambda *_args: renewed.set())
    monkeypatch.setattr(background_worker.queue, 'finish', finish)
    with ThreadPoolExecutor(max_workers=1) as executor:
        work = executor.submit(asyncio.run, background_worker.run_claimed('draft', str(uuid.uuid4()), 'lease',
                                                                         heartbeat_loop=heartbeat_loop))
        try:
            assert finishing.wait(2)
            assert renewed.wait(2), 'Completion waiting on the database must keep its lease'
        finally:
            release.set()
            work.result(timeout=2)


def test_worker_cancellation_cleans_up_all_active_jobs(monkeypatch):
    async def run():
        jobs = iter([('a', 'alice', 'a'), ('b', 'bob', 'b')])
        started, cancelled = set(), set()
        ready = asyncio.Event()

        async def execute(identity, *_args, **_kwargs):
            started.add(identity)
            if len(started) == 2:
                ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.add(identity)

        monkeypatch.setattr(background_worker.queue, 'claim', lambda _engine: next(jobs, None))
        monkeypatch.setattr(background_worker, 'run_claimed', execute)
        worker = asyncio.create_task(background_worker.serve(None, threading.Event()))
        try:
            await asyncio.wait_for(ready.wait(), 2)
        finally:
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker
        assert cancelled == {'a', 'b'}

    asyncio.run(run())
