import asyncio
import threading
import uuid

import pytest

from vendoo_studio.services import background_worker


def test_worker_starts_independent_jobs_before_previous_jobs_finish_and_drains(monkeypatch):
    async def run():
        stopping = threading.Event()
        jobs = iter([('draft-a', 'alice', 'lease-a'), ('draft-b', 'bob', 'lease-b'),
                     ('draft-c', 'alice', 'lease-c')])
        started = []
        all_started, release = asyncio.Event(), asyncio.Event()

        async def execute(*job):
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


def test_worker_failure_logs_location_without_private_exception_text(monkeypatch, caplog):
    finished = []

    async def fail(*_args):
        raise RuntimeError('private database credential')

    def finish(*args, **kwargs):
        finished.append((args, kwargs))

    monkeypatch.setattr(background_worker, '_execute', fail)
    monkeypatch.setattr(background_worker.queue, 'finish', finish)
    asyncio.run(background_worker.run_claimed('test-task', str(uuid.uuid4()), 'test-lease'))
    assert len(finished) == 1 and finished[0][1]['error']
    assert 'RuntimeError' in caplog.text
    assert 'test_background_concurrency.py' in caplog.text
    assert 'private database credential' not in caplog.text


def test_worker_cancellation_cleans_up_all_active_jobs(monkeypatch):
    async def run():
        jobs = iter([('a', 'alice', 'a'), ('b', 'bob', 'b')])
        started, cancelled = set(), set()
        ready = asyncio.Event()

        async def execute(identity, *_args):
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
