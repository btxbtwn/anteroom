"""A deploy runs two Machines for a few minutes; only one runs the schedulers."""
import asyncio
import logging
import socket
import threading
import time
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from test_hosted_service_storage import storage  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from anteroom.services import device_routes, process_handoff


@pytest.fixture(autouse=True)
def fresh_handoff(monkeypatch):
    process_handoff.reset()
    monkeypatch.setattr(process_handoff, 'RENEW_S', 0.01)
    yield
    process_handoff.reset()


def machine(monkeypatch, name, port):
    monkeypatch.setenv('FLY_MACHINE_ID', name)
    monkeypatch.setenv('ANTEROOM_PROCESS_BASE_URL', f'http://127.0.0.1:{port}')


def test_one_machine_holds_the_scheduler_lease_until_it_releases(storage, monkeypatch):  # noqa: F811
    engine, sessions = storage
    owner = str(uuid.uuid4())
    machine(monkeypatch, 'blue', 8080)
    assert process_handoff.hold(engine)
    assert device_routes.claim(owner, 'mac', engine).owned
    machine(monkeypatch, 'green', 8081)
    assert not process_handoff.hold(engine)
    assert not device_routes.claim(owner, 'mac', engine).owned

    machine(monkeypatch, 'blue', 8080)
    assert process_handoff.hold(engine), 'the holder renews its own lease'
    process_handoff.release(engine)

    machine(monkeypatch, 'green', 8081)
    assert process_handoff.hold(engine)
    assert device_routes.claim(owner, 'mac', engine).owned, 'a released Mac route moves at once'
    with sessions(owner)() as db, pytest.raises(DBAPIError):
        db.execute(text("SELECT anteroom_private.hold_process_lease('schedulers', 'blue', 30)")).all()


def test_an_expired_lease_passes_to_the_next_machine(storage, monkeypatch):  # noqa: F811
    engine, _sessions = storage
    machine(monkeypatch, 'blue', 8080)
    assert process_handoff.hold(engine)
    with engine.begin() as connection:
        connection.execute(text("UPDATE anteroom_private.process_leases SET expires_at = now() - interval '1 second'"))
    machine(monkeypatch, 'green', 8081)
    assert process_handoff.hold(engine)


def run_lead(holds, *, until):
    """Run ``lead`` with a scripted lease; returns the ordered events."""
    events = []
    answers = iter(holds)

    def hold(_engine, _name='schedulers'):
        answer = next(answers, holds[-1])
        events.append(('hold', answer))
        return answer

    def start():
        events.append('start')
        return lambda: events.append('stop')

    async def exercise():
        task = asyncio.create_task(process_handoff.lead('engine', {'schedulers': start}, lambda: events.append('drain')))
        for _ in range(500):
            if until(events):
                break
            await asyncio.sleep(0.01)
        process_handoff.begin_draining()
        await asyncio.wait_for(task, 2)

    original = process_handoff.hold, process_handoff.release, process_handoff.release_routes
    process_handoff.hold = hold
    process_handoff.release = lambda _engine: events.append('release')
    process_handoff.release_routes = lambda _engine: events.append('routes')
    try:
        asyncio.run(exercise())
    finally:
        process_handoff.hold, process_handoff.release, process_handoff.release_routes = original
    return events


def test_schedulers_wait_for_the_lease_and_hand_it_over_after_stopping():
    events = run_lead([False, False, True], until=lambda events: 'start' in events)
    assert events[:4] == [('hold', False), ('hold', False), ('hold', True), 'start']
    assert events[-4:] == ['routes', 'drain', 'stop', 'release']


def test_losing_the_lease_stops_the_schedulers():
    events = run_lead([True, False], until=lambda events: 'stop' in events)
    assert events[:4] == [('hold', True), 'start', ('hold', False), 'stop']
    assert events.count('stop') == 1


def drain_with_work(*, finish_after, grace_s=5, renew_as_work_ends=False):
    """Drain ``lead`` while scheduled marketplace work runs; returns the ordered events.

    The work ends ``finish_after`` seconds into the drain (``None``: never), and
    the scheduler set's stop cancels it, like ``BackgroundLoop.stop``.

    The job records its outcome while it is still inside ``marketplace_work``,
    so ``lead`` can renew the lease once more between that record and the work
    being marked idle. ``renew_as_work_ends`` holds the job there until that
    renewal has happened, which makes the order certain instead of occasional.
    """
    events = []
    started = threading.Event()
    finish = threading.Event()
    outcome_recorded = threading.Event()
    renewed_after_outcome = threading.Event()
    waited_for_renewal = []

    def hold(_engine, _name='schedulers'):
        late = outcome_recorded.is_set()
        events.append(('hold', process_handoff.draining()))
        if late:
            renewed_after_outcome.set()
        return True

    def start():
        def job():
            with process_handoff.marketplace_work():
                started.set()
                finish.wait(5)
                events.append('job finished' if not cancelled.is_set() else 'job cancelled')
                outcome_recorded.set()
                if renew_as_work_ends and not cancelled.is_set():
                    # Checked on the test's own thread below: an assert here would only warn.
                    waited_for_renewal.append(renewed_after_outcome.wait(2))

        cancelled = threading.Event()
        thread = threading.Thread(target=job)
        thread.start()

        def stop():
            cancelled.set()
            finish.set()
            thread.join()
            events.append('stop')
        return stop

    async def exercise():
        task = asyncio.create_task(process_handoff.lead(
            'engine', {'schedulers': start}, lambda: events.append('drain'), grace_s=grace_s,
        ))
        assert await asyncio.to_thread(started.wait, 2)
        process_handoff.begin_draining()
        if finish_after is not None:
            await asyncio.sleep(finish_after)
            finish.set()
        await asyncio.wait_for(task, 5)

    original = process_handoff.hold, process_handoff.release, process_handoff.release_routes
    process_handoff.hold = hold
    process_handoff.release = lambda _engine: events.append('release')
    process_handoff.release_routes = lambda _engine: events.append('routes')
    try:
        asyncio.run(exercise())
    finally:
        process_handoff.hold, process_handoff.release, process_handoff.release_routes = original
    if renew_as_work_ends:
        assert 'job cancelled' not in events, events
        assert waited_for_renewal == [True], f'the lease was not renewed while the work was still in flight: {events}'
    return events


def finished_then_stopped_then_released(events):
    """The work ran to its end under a renewed lease; only then did the set stop and the lease go."""
    drained = events[events.index('drain'):]
    assert 'job cancelled' not in events, 'the drain cancelled work it should have let finish'
    finished = drained.index('job finished')
    assert ('hold', True) in drained[:finished], 'the stopping Machine keeps renewing the lease while the work runs'
    # ``lead`` may renew once more as the work ends: the job records its outcome
    # before ``marketplace_work`` marks it idle. Nothing else may come between
    # the work finishing and the stop, and the release is always last.
    after = drained[finished + 1:]
    assert after[-2:] == ['stop', 'release']
    assert all(event == ('hold', True) for event in after[:-2]), after
    assert events.count('stop') == 1 and events.count('release') == 1
    return after[:-2]


def drained_promptly(**work):
    """Drain with work that ends 0.2 s in; ``lead`` must hand over then, not wait out the 5 s grace."""
    started = time.monotonic()
    events = drain_with_work(finish_after=0.2, **work)
    assert time.monotonic() - started < 2, 'the drain kept waiting after the work had finished'
    return events


def test_a_running_relist_finishes_during_the_drain_while_the_lease_is_held():
    finished_then_stopped_then_released(drained_promptly())


def test_a_lease_renewal_as_the_relist_ends_still_stops_before_releasing():
    late_renewals = finished_then_stopped_then_released(drained_promptly(renew_as_work_ends=True))
    assert late_renewals, 'this run is meant to renew the lease after the job recorded its outcome'


def test_a_draining_machine_hands_its_devices_over_before_the_grace():
    events = drain_with_work(finish_after=0.2)
    routes = events.index('routes')
    assert routes < events.index('drain')
    assert ('hold', True) in events[routes:events.index('job finished')], 'the lease is still renewed after the routes go'
    assert events.count('routes') == 1


def test_releasing_routes_keeps_the_scheduler_lease(storage, monkeypatch):  # noqa: F811
    engine, sessions = storage
    owner = str(uuid.uuid4())
    machine(monkeypatch, 'blue', 8080)
    assert process_handoff.hold(engine)
    assert device_routes.claim(owner, 'chrome', engine).owned
    process_handoff.release_routes(engine)

    machine(monkeypatch, 'green', 8081)
    assert device_routes.claim(owner, 'chrome', engine).owned, 'the Chrome route moves while blue drains'
    assert not process_handoff.hold(engine), 'blue still holds the scheduler lease'
    with sessions(owner)() as db, pytest.raises(DBAPIError):
        db.execute(text("SELECT anteroom_private.release_machine_routes('green')")).all()


def test_the_drain_grace_is_bounded():
    started = time.monotonic()
    events = drain_with_work(finish_after=None, grace_s=0.2)
    assert time.monotonic() - started < 2
    assert events[-3:] == ['job cancelled', 'stop', 'release']


def test_a_drain_with_nothing_running_hands_over_at_once():
    started = time.monotonic()
    events = run_lead([True], until=lambda events: 'start' in events)
    assert time.monotonic() - started < process_handoff.DRAIN_GRACE_S
    assert events[-4:] == ['routes', 'drain', 'stop', 'release']


def test_the_shutdown_waits_stay_under_fly_kill_timeout():
    import re
    from pathlib import Path

    fly = (Path(__file__).resolve().parents[3] / 'deploy' / 'fly' / 'fly.toml').read_text()
    kill_timeout = int(re.search(r'^kill_timeout = (\d+)', fly, re.M).group(1))
    # Uvicorn drains requests (120 s), then hosted.py waits for the handoff and
    # the draft worker (120 s) side by side, then stops each loop (up to 20 s).
    assert 120 + max(process_handoff.HANDOFF_WAIT_S, 120) + 20 < kill_timeout
    assert process_handoff.HANDOFF_WAIT_S >= process_handoff.DRAIN_GRACE_S + 40, "two scheduler sets stop one after the other"


def test_scheduled_publishes_and_relists_count_as_marketplace_work(monkeypatch):
    from anteroom.services import auto_relist, automation_scheduler, draft_scheduling

    seen = []

    async def run(**_kwargs):
        seen.append(process_handoff._idle.is_set())
        return []

    monkeypatch.setattr(auto_relist, 'run_auto_relist', run)
    monkeypatch.setattr(draft_scheduling, 'run_due_scheduled_publishes', run)
    asyncio.run(automation_scheduler._run_auto_relist(automation_scheduler.as_utc(datetime.now(UTC))))
    asyncio.run(automation_scheduler._run_scheduled_drafts(automation_scheduler.as_utc(datetime.now(UTC))))
    assert seen == [False, False]
    assert process_handoff._idle.is_set()


def test_a_stopping_machine_starts_no_new_automation_tick(monkeypatch):
    from anteroom.services import hosted_automation_scheduler

    ticked = []
    monkeypatch.setattr(hosted_automation_scheduler, 'due_owners', lambda _engine: ['seller'])
    monkeypatch.setattr(hosted_automation_scheduler, 'TICK_SECONDS', 0.01)

    async def run_owner(owner):
        ticked.append(owner)

    monkeypatch.setattr(hosted_automation_scheduler, 'run_owner', run_owner)
    process_handoff.begin_draining()

    async def exercise():
        task = asyncio.create_task(hosted_automation_scheduler.serve('engine'))
        await asyncio.sleep(0.1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
    assert ticked == []


def test_a_machine_without_background_work_still_releases_its_devices():
    events = []

    async def exercise():
        task = asyncio.create_task(process_handoff.lead('engine', {}, lambda: events.append('drain')))
        await asyncio.sleep(0.05)
        process_handoff.begin_draining()
        await asyncio.wait_for(task, 2)

    release, release_routes = process_handoff.release, process_handoff.release_routes
    process_handoff.release = lambda _engine: events.append('release')
    process_handoff.release_routes = lambda _engine: events.append('routes')
    try:
        asyncio.run(exercise())
    finally:
        process_handoff.release, process_handoff.release_routes = release, release_routes
    assert events == ['routes', 'drain', 'release']


def test_a_machine_waiting_for_the_lease_runs_drafts_and_offers_every_feature(monkeypatch):
    from anteroom.hosted import create_app
    from anteroom.routes.hosted_workspace import runtime
    from anteroom.services import background_worker

    monkeypatch.setenv('ANTEROOM_BACKGROUND_WORKER', '1')
    monkeypatch.setattr(process_handoff, 'hold', lambda _engine, _name='schedulers': False)
    monkeypatch.setattr(process_handoff, 'release', lambda _engine: None)
    drafts = threading.Event()

    async def worker(_engine, stopping):
        drafts.set()
        while not stopping.is_set():
            await asyncio.sleep(.01)

    async def must_not_run(*_args):
        raise AssertionError('a scheduler ran without the lease')

    monkeypatch.setattr(background_worker, 'serve', worker)
    for name in ('hosted_automation_scheduler', 'hosted_offer_scheduler', 'hosted_job_watchdog',
                 'hosted_sale_checks', 'hosted_promotion_scheduler',
                 'hosted_sales_scheduler'):
        monkeypatch.setattr(f'anteroom.services.{name}.serve', must_not_run)
    with TestClient(create_app('https://example.supabase.co')) as client:
        assert drafts.wait(2)
        features = runtime(SimpleNamespace(app=client.app), db=_NoCategories())
        assert {'ebay_offers', 'marketplace_sessions', 'generation'} <= set(features['features'])
        assert features['action_marketplaces']


class _NoCategories:
    def scalar(self, *_args, **_kwargs):
        return None


def test_the_server_takes_ipv4_and_ipv6_connections():
    from anteroom.hosted_server import dual_stack_socket

    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    sock = dual_stack_socket(port)
    sock.listen()
    try:
        for family, host in ((socket.AF_INET, '127.0.0.1'), (socket.AF_INET6, '::1')):
            with socket.socket(family, socket.SOCK_STREAM) as client:
                client.settimeout(2)
                client.connect((host, port))
                accepted, _ = sock.accept()
                accepted.close()
    finally:
        sock.close()


def test_a_stop_signal_starts_the_handoff_before_requests_drain(monkeypatch):
    import signal
    import uvicorn
    from anteroom.hosted_server import HostedServer

    server = HostedServer(uvicorn.Config('anteroom.hosted:app'))
    with monkeypatch.context() as patch:
        patch.setattr(process_handoff, 'clear_route_cache', lambda: pytest.fail('signal callback took the cache lock'))
        server.handle_exit(signal.SIGTERM, None)
    assert process_handoff.draining()
    assert server.should_exit


SCHEDULERS = ('hosted_automation_scheduler', 'hosted_offer_scheduler', 'hosted_job_watchdog',
              'hosted_sales_scheduler')
CHROME_SCHEDULERS = ('hosted_sale_checks', 'hosted_promotion_scheduler')


@pytest.mark.parametrize('role,jobs', [
    # Chrome tabs connect to the web Machine, and only it knows which are connected.
    ('web', {'chrome': set(CHROME_SCHEDULERS)}),
    ('worker', {'schedulers': set(SCHEDULERS)}),
    ('all', {'schedulers': set(SCHEDULERS), 'chrome': set(CHROME_SCHEDULERS)}),
])
def test_each_role_runs_the_schedulers_its_connections_need(monkeypatch, role, jobs):
    from anteroom.hosted import create_app
    from anteroom.services import background_worker

    monkeypatch.setenv('ANTEROOM_BACKGROUND_WORKER', '1')
    monkeypatch.setenv('ANTEROOM_PROCESS_ROLE', role)
    captured, led = {}, threading.Event()

    async def lead(_engine, starts, _on_drain):
        captured.update(starts)
        led.set()

    async def idle(*_args):
        return None

    monkeypatch.setattr(process_handoff, 'lead', lead)
    monkeypatch.setattr(background_worker, 'serve', idle)
    ran = set()
    for name in SCHEDULERS + CHROME_SCHEDULERS:
        def serve(*_args, _name=name):
            ran.add(_name)
            return idle()
        monkeypatch.setattr(f'anteroom.services.{name}.serve', serve)
    with TestClient(create_app('https://example.supabase.co')):
        assert led.wait(2)
    assert set(captured) == set(jobs)
    for lease, start in captured.items():
        ran.clear()
        start()()
        assert ran == jobs[lease], lease


def test_unexpected_scheduler_exits_log_critical(monkeypatch, caplog):
    from anteroom.hosted import create_app
    from anteroom.services import hosted_promotion_scheduler, hosted_sale_checks

    monkeypatch.setenv('ANTEROOM_BACKGROUND_WORKER', '1')
    monkeypatch.setenv('ANTEROOM_PROCESS_ROLE', 'web')
    monkeypatch.setattr(process_handoff, 'hold', lambda _engine, _name: True)
    monkeypatch.setattr(process_handoff, 'release', lambda _engine: None)

    failure = RuntimeError('sale scheduler crashed')

    async def crash():
        raise failure

    async def finish():
        return

    monkeypatch.setattr(hosted_sale_checks, 'serve', crash)
    monkeypatch.setattr(hosted_promotion_scheduler, 'serve', finish)

    reported = threading.Event()
    records = []
    emit = caplog.handler.emit

    def capture(record):
        emit(record)
        if record.name == 'anteroom.hosted' and record.levelno == logging.CRITICAL:
            records.append(record)
            if len(records) == 2:
                reported.set()

    monkeypatch.setattr(caplog.handler, 'emit', capture)
    with TestClient(create_app('https://example.supabase.co')):
        assert reported.wait(5), 'both scheduler exits must be logged while the host is running'

    crashed = next(record for record in records if 'anteroom-chrome-sales' in record.getMessage())
    finished = next(record for record in records if 'anteroom-promotions' in record.getMessage())
    assert crashed.exc_info and crashed.exc_info[1] is failure
    assert not finished.exc_info or finished.exc_info[1] is None


def test_scheduler_lease_loss_does_not_log_critical(monkeypatch, caplog):
    from anteroom.hosted import create_app
    from anteroom.services import hosted_promotion_scheduler, hosted_sale_checks

    monkeypatch.setenv('ANTEROOM_BACKGROUND_WORKER', '1')
    monkeypatch.setenv('ANTEROOM_PROCESS_ROLE', 'web')
    lost_lease = threading.Event()
    started = threading.Event()
    stopped = threading.Event()
    checked_after_stop = threading.Event()

    def hold(_engine, _name):
        if stopped.is_set():
            checked_after_stop.set()
        return not lost_lease.is_set()

    monkeypatch.setattr(process_handoff, 'hold', hold)
    monkeypatch.setattr(process_handoff, 'release', lambda _engine: None)

    async def idle():
        await asyncio.Future()

    async def monitor_sales():
        started.set()
        try:
            await idle()
        finally:
            stopped.set()

    monkeypatch.setattr(hosted_sale_checks, 'serve', monitor_sales)
    monkeypatch.setattr(hosted_promotion_scheduler, 'serve', idle)
    with TestClient(create_app('https://example.supabase.co')):
        assert started.wait(5)
        lost_lease.set()
        assert checked_after_stop.wait(5)

    assert not any(record.name == 'anteroom.hosted' and record.levelno == logging.CRITICAL
                   for record in caplog.records)


def test_dead_draft_worker_outside_shutdown_triggers_server_exit_and_nonzero_status(monkeypatch):
    import uvicorn
    from anteroom import hosted
    from anteroom.hosted import create_app, set_worker_stopped_handler
    from anteroom.hosted_server import HostedServer
    from anteroom.services import background_worker

    monkeypatch.setenv('ANTEROOM_BACKGROUND_WORKER', '1')
    monkeypatch.setenv('ANTEROOM_PROCESS_ROLE', 'worker')
    monkeypatch.setattr(process_handoff, 'hold', lambda _engine, _name='schedulers': False)
    monkeypatch.setattr(process_handoff, 'release', lambda _engine: None)

    server = HostedServer(uvicorn.Config('anteroom.hosted:app'))
    notified = threading.Event()
    seen_exc: list[BaseException | None] = []

    def on_stop(exc: BaseException | None) -> None:
        seen_exc.append(exc)
        server.exit_on_worker_failure(exc)
        notified.set()

    async def crashing_worker(_engine, _stopping):
        raise RuntimeError('draft loop crashed')

    monkeypatch.setattr(background_worker, 'serve', crashing_worker)
    set_worker_stopped_handler(on_stop)
    try:
        with TestClient(create_app('https://example.supabase.co')):
            assert notified.wait(2)
    finally:
        set_worker_stopped_handler(None)

    assert len(seen_exc) == 1 and isinstance(seen_exc[0], RuntimeError)
    assert server.worker_failed is True
    assert server.should_exit is True
    assert process_handoff.draining() is True
    assert hosted._on_worker_stopped is None


def test_clean_shutdown_does_not_trigger_worker_failure_handler(monkeypatch):
    from anteroom.hosted import create_app, set_worker_stopped_handler
    from anteroom.services import background_worker

    monkeypatch.setenv('ANTEROOM_BACKGROUND_WORKER', '1')
    monkeypatch.setenv('ANTEROOM_PROCESS_ROLE', 'worker')
    monkeypatch.setattr(process_handoff, 'hold', lambda _engine, _name='schedulers': False)
    monkeypatch.setattr(process_handoff, 'release', lambda _engine: None)

    started = threading.Event()
    triggered: list[BaseException | None] = []

    async def healthy_worker(_engine, stopping):
        started.set()
        while not stopping.is_set():
            await asyncio.sleep(0.01)

    monkeypatch.setattr(background_worker, 'serve', healthy_worker)
    set_worker_stopped_handler(triggered.append)
    try:
        with TestClient(create_app('https://example.supabase.co')):
            assert started.wait(2)
    finally:
        set_worker_stopped_handler(None)

    assert triggered == []


def test_hosted_server_main_exits_nonzero_when_worker_failed(monkeypatch):
    from anteroom import hosted, hosted_server

    registered: list[object] = []

    class FakeServer:
        def __init__(self, _config):
            self.worker_failed = False

        def exit_on_worker_failure(self, _exc=None):
            self.worker_failed = True

        def run(self, sockets=None):
            assert sockets
            # Simulate the worker callback firing while uvicorn is serving.
            self.exit_on_worker_failure(RuntimeError('boom'))

    monkeypatch.setattr(hosted_server, 'HostedServer', FakeServer)
    monkeypatch.setattr(hosted_server, 'dual_stack_socket', lambda _port: object())
    monkeypatch.setattr(hosted, 'set_worker_stopped_handler', registered.append)

    with pytest.raises(SystemExit) as exited:
        hosted_server.main()
    assert exited.value.code == 1
    assert registered[-1] is None
