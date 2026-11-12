"""A deploy runs two Machines for a few minutes; only one runs the schedulers."""
import asyncio
import socket
import threading
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from test_hosted_service_storage import storage  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from vendoo_studio.services import device_routes, process_handoff


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

    original = process_handoff.hold, process_handoff.release
    process_handoff.hold = hold
    process_handoff.release = lambda _engine: events.append('release')
    try:
        asyncio.run(exercise())
    finally:
        process_handoff.hold, process_handoff.release = original
    return events


def test_schedulers_wait_for_the_lease_and_hand_it_over_after_stopping():
    events = run_lead([False, False, True], until=lambda events: 'start' in events)
    assert events[:4] == [('hold', False), ('hold', False), ('hold', True), 'start']
    assert events[-3:] == ['drain', 'stop', 'release']


def test_losing_the_lease_stops_the_schedulers():
    events = run_lead([True, False], until=lambda events: 'stop' in events)
    assert events[:4] == [('hold', True), 'start', ('hold', False), 'stop']
    assert events.count('stop') == 1


def test_a_machine_without_background_work_still_releases_its_devices():
    events = []

    async def exercise():
        task = asyncio.create_task(process_handoff.lead('engine', {}, lambda: events.append('drain')))
        await asyncio.sleep(0.05)
        process_handoff.begin_draining()
        await asyncio.wait_for(task, 2)

    release = process_handoff.release
    process_handoff.release = lambda _engine: events.append('release')
    try:
        asyncio.run(exercise())
    finally:
        process_handoff.release = release
    assert events == ['drain', 'release']


def test_a_machine_waiting_for_the_lease_runs_drafts_and_offers_every_feature(monkeypatch):
    from vendoo_studio.hosted import create_app
    from vendoo_studio.routes.hosted_workspace import runtime
    from vendoo_studio.services import background_worker

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
                 'hosted_sourcing_scheduler', 'hosted_sale_checks', 'hosted_promotion_scheduler',
                 'hosted_sales_scheduler'):
        monkeypatch.setattr(f'vendoo_studio.services.{name}.serve', must_not_run)
    with TestClient(create_app('https://example.supabase.co')) as client:
        assert drafts.wait(2)
        features = runtime(SimpleNamespace(app=client.app), db=_NoCategories())
        assert {'ebay_offers', 'marketplace_sessions', 'generation'} <= set(features['features'])
        assert features['action_marketplaces']


class _NoCategories:
    def scalar(self, *_args, **_kwargs):
        return None


def test_the_server_takes_ipv4_and_ipv6_connections():
    from vendoo_studio.hosted_server import dual_stack_socket

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


def test_a_stop_signal_starts_the_handoff_before_requests_drain():
    import signal
    import uvicorn
    from vendoo_studio.hosted_server import HostedServer

    server = HostedServer(uvicorn.Config('vendoo_studio.hosted:app'))
    server.handle_exit(signal.SIGTERM, None)
    assert process_handoff.draining()
    assert server.should_exit


SCHEDULERS = ('hosted_automation_scheduler', 'hosted_offer_scheduler', 'hosted_job_watchdog',
              'hosted_sourcing_scheduler', 'hosted_sales_scheduler')
CHROME_SCHEDULERS = ('hosted_sale_checks', 'hosted_promotion_scheduler')


@pytest.mark.parametrize('role,jobs', [
    # Chrome tabs connect to the web Machine, and only it knows which are connected.
    ('web', {'chrome': set(CHROME_SCHEDULERS)}),
    ('worker', {'schedulers': set(SCHEDULERS)}),
    ('all', {'schedulers': set(SCHEDULERS), 'chrome': set(CHROME_SCHEDULERS)}),
])
def test_each_role_runs_the_schedulers_its_connections_need(monkeypatch, role, jobs):
    from vendoo_studio.hosted import create_app
    from vendoo_studio.services import background_worker

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
        monkeypatch.setattr(f'vendoo_studio.services.{name}.serve', serve)
    with TestClient(create_app('https://example.supabase.co')):
        assert led.wait(2)
    assert set(captured) == set(jobs)
    for lease, start in captured.items():
        ran.clear()
        start()()
        assert ran == jobs[lease], lease
