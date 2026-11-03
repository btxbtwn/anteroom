"""A deployment drains the claimed draft without starting another one."""
import asyncio
from threading import Event
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from vendoo_studio.services import background_worker


def test_shutdown_finishes_claimed_work_before_leaving(monkeypatch):
    stopping = Event()
    claims, finished = [], []

    def claim(engine):
        if claims:
            return None
        claims.append(engine)
        return ('draft-task', 'seller', 'lease')

    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()

        async def execute(*claimed, **_kwargs):
            started.set()
            await release.wait()
            finished.append(claimed)

        monkeypatch.setattr(background_worker.queue, 'claim', claim)
        monkeypatch.setattr(background_worker, 'run_claimed', execute)
        worker = asyncio.create_task(background_worker.serve('engine', stopping))
        await asyncio.wait_for(started.wait(), 2)
        stopping.set()
        await asyncio.sleep(0)
        assert not worker.done()
        release.set()
        await asyncio.wait_for(worker, 2)

    asyncio.run(exercise())
    assert claims == ['engine']
    assert finished == [('draft-task', 'seller', 'lease')]


def test_stopped_worker_does_not_claim_more_work(monkeypatch):
    stopping = Event()
    stopping.set()
    def claim(_engine):
        raise AssertionError('claimed a draft after shutdown started')
    monkeypatch.setattr(background_worker.queue, 'claim', claim)
    asyncio.run(background_worker.serve('engine', stopping))


def test_hosted_shutdown_drains_a_draft_after_its_browser_disconnects(monkeypatch):
    from vendoo_studio.hosted import create_app

    monkeypatch.setenv('ANTEROOM_BACKGROUND_WORKER', '1')
    started, stopping, release, finished = Event(), Event(), Event(), Event()
    original_serve = background_worker.serve

    async def tracked_worker(engine, requested):
        async def watch_shutdown():
            while not requested.is_set():
                await asyncio.sleep(.01)
            stopping.set()
        watcher = asyncio.create_task(watch_shutdown())
        try:
            await original_serve(engine, requested)
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)

    async def execute(*_claimed, **_kwargs):
        started.set()
        await asyncio.to_thread(release.wait)
        finished.set()

    async def scheduler(_engine):
        await asyncio.Event().wait()

    monkeypatch.setattr(background_worker, 'serve', tracked_worker)
    jobs = iter([('draft', 'seller', 'lease')])
    monkeypatch.setattr(background_worker.queue, 'claim', lambda _engine: next(jobs, None))
    monkeypatch.setattr(background_worker, 'run_claimed', execute)
    for name in ('hosted_automation_scheduler', 'hosted_offer_scheduler', 'hosted_job_watchdog'):
        monkeypatch.setattr(f'vendoo_studio.services.{name}.serve', scheduler)
    client = TestClient(create_app('https://example.supabase.co'))
    client.__enter__()
    if not started.wait(2):
        release.set()
        client.__exit__(None, None, None)
        raise AssertionError('The draft worker did not start')
    with ThreadPoolExecutor(max_workers=1) as executor:
        closing = executor.submit(client.__exit__, None, None, None)
        try:
            assert stopping.wait(2)
            assert not closing.done()
            assert not finished.is_set()
        finally:
            release.set()
            closing.result(timeout=5)
    assert finished.is_set()


def test_blocked_marketplace_scheduler_cannot_stop_draft_heartbeats(monkeypatch):
    from vendoo_studio import hosted

    monkeypatch.setenv('ANTEROOM_BACKGROUND_WORKER', '1')
    blocked, release, pulse = Event(), Event(), Event()
    loops = []
    original_loop = hosted.BackgroundLoop

    def tracked_loop(*args, **kwargs):
        loop = original_loop(*args, **kwargs)
        loops.append(loop)
        return loop

    async def worker(_engine, stopping):
        await asyncio.to_thread(blocked.wait)
        pulse.set()
        while not stopping.is_set():
            await asyncio.sleep(.01)

    async def blocking_offers(_engine):
        blocked.set()
        # Reproduce a synchronous database wait in a scheduler coroutine.
        release.wait(timeout=5)
        await asyncio.Event().wait()

    async def idle(_engine):
        await asyncio.Event().wait()

    monkeypatch.setattr(hosted, 'BackgroundLoop', tracked_loop)
    monkeypatch.setattr(background_worker, 'serve', worker)
    monkeypatch.setattr('vendoo_studio.services.hosted_offer_scheduler.serve', blocking_offers)
    for name in ('hosted_automation_scheduler', 'hosted_job_watchdog', 'hosted_sourcing_scheduler'):
        monkeypatch.setattr(f'vendoo_studio.services.{name}.serve', idle)
    with TestClient(hosted.create_app('https://example.supabase.co')) as client:
        try:
            assert blocked.wait(2)
            assert pulse.wait(1), 'Marketplace database waits must not interrupt draft lease renewal'
            assert client.get('/api/health').status_code == 200
        finally:
            release.set()
    assert all(not loop.thread.is_alive() for loop in loops)
