"""Mac and Chrome commands stay on the process the device is polling."""
import asyncio
import threading
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.pool import QueuePool
from sqlalchemy.exc import DBAPIError

from test_hosted_service_storage import storage  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from anteroom.database import make_engine
from anteroom.hosted import create_app
from anteroom.services import browser_bridge, device_routes
from anteroom.services.device_routes import RoutingUnavailable, claim
from anteroom.services.hosted_browser_relay import BrowserRelay, ChromeReadError
from anteroom.services.mac_inference import MacInference, MacUnavailable, relay_for

OWNER = 'c7a5ee16-9850-49ba-b5bd-83b8be54df46'
MODELS = {'chatgpt': {'vision_model': 'v', 'listing_model': 'l'}}


async def _forward_to_peer(owner, device):
    return 'http://127.0.0.1:8081'


def test_hosted_process_reuses_a_few_postgres_connections(monkeypatch):
    monkeypatch.setenv('ANTEROOM_HOSTED', '1')
    pooled = make_engine('postgresql+psycopg://anteroom_api:secret@127.0.0.1:6543/postgres')
    try:
        assert isinstance(pooled.pool, QueuePool)
        assert pooled.pool._pool.maxsize == 8
        assert pooled.pool._max_overflow == 4
    finally:
        pooled.dispose()
    monkeypatch.delenv('ANTEROOM_HOSTED', raising=False)
    fresh = make_engine('postgresql+psycopg://anteroom_api:secret@127.0.0.1:6543/postgres')
    try:
        assert fresh.pool.__class__.__name__ == 'NullPool'
    finally:
        fresh.dispose()


def test_web_process_does_not_claim_background_work(monkeypatch):
    monkeypatch.setenv('ANTEROOM_BACKGROUND_WORKER', '1')
    monkeypatch.setenv('ANTEROOM_PROCESS_ROLE', 'web')
    assert create_app().state.background_worker_enabled is False
    monkeypatch.setenv('ANTEROOM_PROCESS_ROLE', 'worker')
    assert create_app().state.background_worker_enabled is True


def test_web_process_queues_drafts_for_the_worker_machines(monkeypatch):
    from anteroom.routes.hosted_tasks import drafts_run, require_worker, schedules_run

    monkeypatch.setenv('ANTEROOM_BACKGROUND_WORKER', '1')
    monkeypatch.setenv('ANTEROOM_PROCESS_ROLE', 'web')
    web = create_app()
    # No draft worker or schedulers run here; the worker group runs them.
    assert getattr(web.state, 'background_worker', None) is None
    assert drafts_run(web) and schedules_run(web)
    require_worker(SimpleNamespace(app=web))

    monkeypatch.setenv('ANTEROOM_PROCESS_ROLE', 'all')
    alone = create_app()
    # A single process without a running worker still refuses work it cannot run.
    assert not drafts_run(alone) and not schedules_run(alone)
    with pytest.raises(HTTPException) as refused:
        require_worker(SimpleNamespace(app=alone))
    assert refused.value.status_code == 503


def test_a_fresh_device_route_cannot_be_stolen_or_read_by_the_seller(storage, monkeypatch):  # noqa: F811
    engine, sessions = storage
    owner = str(uuid.uuid4())
    monkeypatch.setenv('FLY_MACHINE_ID', 'machinea')
    monkeypatch.setenv('ANTEROOM_PROCESS_BASE_URL', 'http://127.0.0.1:8080')
    first = claim(owner, 'mac', engine)
    assert first.owned and first.machine_id == 'machinea'
    monkeypatch.setenv('FLY_MACHINE_ID', 'machineb')
    monkeypatch.setenv('ANTEROOM_PROCESS_BASE_URL', 'http://127.0.0.1:8081')
    second = claim(owner, 'mac', engine)
    assert second == first.__class__('machinea', 'http://127.0.0.1:8080', False)
    with engine.begin() as connection:
        connection.execute(text("UPDATE anteroom_private.device_routes SET seen_at = now() - interval '2 minutes'"))
    taken = claim(owner, 'mac', engine)
    assert taken.owned and taken.machine_id == 'machineb'
    with sessions(owner)() as db, pytest.raises(DBAPIError):
        db.execute(text('SELECT * FROM anteroom_private.device_routes')).all()
    with sessions(owner)() as db, pytest.raises(DBAPIError):
        db.execute(text(
            "SELECT * FROM anteroom_private.note_device_route(cast(:owner AS uuid), 'mac', 'machineb', "
            "'http://127.0.0.1:8081', 45)"
        ), {'owner': owner}).all()


@pytest.mark.parametrize('subscription', ['chatgpt', 'claude'])
def test_forwarded_inference_does_not_queue_on_the_wrong_process(monkeypatch, subscription):
    relay = MacInference(OWNER)
    relay.poll('a' * 32, MODELS, timeout=0)
    calls = []

    async def fail(url, path, body, timeout):
        assert body['provider'] == subscription
        calls.append(path)
        raise RoutingUnavailable

    monkeypatch.setattr(device_routes, 'forward_target', _forward_to_peer)
    monkeypatch.setattr(device_routes, 'post', fail)
    with pytest.raises(MacUnavailable):
        asyncio.run(relay.invoke('quick_chat', [1], {}, subscription))
    assert calls == ['/internal/mac-inference/invoke']
    assert relay.poll('a' * 32, MODELS, timeout=0) is None
    assert not relay.pending


def test_a_forwarded_call_is_recorded_with_the_model_the_mac_reported(monkeypatch):
    from anteroom.services import model_usage

    # The worker process holds no Mac connection, so only the peer knows its models.
    relay = MacInference(OWNER)

    async def answer(url, path, body, timeout):
        return {'ok': True, 'result': 7, 'model': 'peer-listing'}

    monkeypatch.setattr(device_routes, 'forward_target', _forward_to_peer)
    monkeypatch.setattr(device_routes, 'post', answer)
    with model_usage.capture() as calls:
        assert asyncio.run(relay.invoke('quick_chat', [1], {}, 'chatgpt')) == 7
    assert [(call['provider'], call['model'], call['outcome']) for call in calls] == [('chatgpt', 'peer-listing', 'done')]


@pytest.mark.parametrize('subscription', ['chatgpt', 'claude'])
def test_internal_invoke_reaches_only_that_sellers_mac(monkeypatch, subscription):
    monkeypatch.setenv('ANTEROOM_PROCESS_TOKEN', 'process-token')
    owner = str(uuid.uuid4())
    other = str(uuid.uuid4())
    relay = relay_for(owner)
    models = {subscription: {'vision_model': 'v', 'listing_model': 'l'}}
    relay_for(other).poll('b' * 32, models, timeout=0)
    relay.poll('a' * 32, models, timeout=0)
    app = create_app()

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url='http://internal') as client:
            refused = await client.post('/internal/mac-inference/invoke', json={
                'user_id': owner, 'provider': subscription, 'operation': 'quick_chat', 'args': [1], 'kwargs': {},
            })
            assert refused.status_code == 404

            async def call():
                return await client.post('/internal/mac-inference/invoke', headers={'Authorization': 'Bearer process-token'}, json={
                    'user_id': owner, 'provider': subscription, 'operation': 'quick_chat', 'args': [1], 'kwargs': {},
                })

            task = asyncio.create_task(call())
            command = None
            for _ in range(50):
                command = relay.poll('a' * 32, models, timeout=0)
                if command is not None:
                    break
                await asyncio.sleep(0.01)
            assert command is not None
            assert command['provider'] == subscription
            assert relay_for(other).poll('b' * 32, models, timeout=0) is None
            assert relay.reply('a' * 32, command['id'], 7, False)
            response = await task
            assert response.status_code == 200
            assert response.json() == {'ok': True, 'result': 7, 'model': 'l'}
            task.cancel()

    asyncio.run(run())


def test_chrome_read_forwards_once_and_a_marketplace_request_is_not_retried(monkeypatch):
    relay = BrowserRelay(OWNER)
    calls = []

    async def once(url, path, body, timeout):
        calls.append(path)
        if path.endswith('/request'):
            return {'ok': False, 'error': 'Chrome did not answer in time. Try again.'}
        return {'ok': True, 'reply': {'signed_in': True}}

    monkeypatch.setattr(device_routes, 'forward_target', _forward_to_peer)
    monkeypatch.setattr(device_routes, 'post', once)
    assert asyncio.run(relay.read('marketplace.sessions', {}, 5)) == {'signed_in': True}
    relay.note_seen({'marketplace.actions'})
    monkeypatch.setattr(browser_bridge, '_manager', lambda: relay)
    with pytest.raises(browser_bridge.BrowserBridgeError, match='did not answer'):
        asyncio.run(browser_bridge.request(SimpleNamespace(id='job-1'), 'marketplace.result', {'operation_id': 'op'}))
    assert calls == ['/internal/browser-relay/read', '/internal/browser-relay/request']
    assert not relay._outbox


def test_a_marketplace_action_forwarded_from_the_worker_reaches_chrome_on_the_web_process(monkeypatch):
    """The worker hands a List or Save draft to the web process with the action's own deadline."""
    from anteroom.services.native_marketplaces import EXECUTE_TIMEOUT_S

    monkeypatch.setenv('ANTEROOM_PROCESS_TOKEN', 'process-token')
    owner = str(uuid.uuid4())
    web_relay = BrowserRelay(owner)
    web_relay.note_seen({'marketplace.actions'})
    monkeypatch.setattr('anteroom.routes.internal_devices.chrome_for', lambda seller: web_relay)
    worker_relay = BrowserRelay(owner)
    monkeypatch.setattr(browser_bridge, '_manager', lambda: worker_relay)
    monkeypatch.setattr(device_routes, 'forward_target', _forward_to_peer)
    monkeypatch.setattr(worker_relay.__class__, 'connected', property(lambda self: True))
    app = create_app()

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url='http://internal') as client:
            async def post(url, path, body, timeout):
                response = await client.post(path, json=body, headers={'Authorization': 'Bearer process-token'})
                if response.status_code != 200:
                    raise RoutingUnavailable
                return response.json()

            monkeypatch.setattr(device_routes, 'post', post)
            monkeypatch.setattr('anteroom.services.listing_mode.require', lambda mode: None)
            task = asyncio.create_task(browser_bridge.request(
                SimpleNamespace(id='job-1'), 'marketplace.execute',
                {'marketplace': 'depop', 'operation_id': 'job-1'}, timeout=EXECUTE_TIMEOUT_S))
            for _ in range(200):
                if web_relay._outbox or task.done():
                    break
                await asyncio.sleep(0.01)
            assert not task.done(), task.exception()
            command = web_relay._outbox.popleft()
            assert command['type'] == 'marketplace.execute'
            web_relay.resolve_wait(command['message_id'], {'ok': True, 'state': 'draft'})
            assert await task == {'ok': True, 'state': 'draft'}

    asyncio.run(run())


def test_the_internal_chrome_routes_accept_every_relayed_deadline():
    from anteroom.services.hosted_browser_relay import LONGEST_WAIT_S
    from anteroom.services.native_marketplaces import EXECUTE_TIMEOUT_S
    from anteroom.services.order_history import READ_TIMEOUT_S as SALES_HISTORY_S

    assert max(EXECUTE_TIMEOUT_S, SALES_HISTORY_S) <= LONGEST_WAIT_S


def test_chrome_read_looks_up_the_device_route_off_the_event_loop(monkeypatch):
    # A full pool makes a route lookup wait for a connection. On the request
    # loop that wait froze every request and kept the pool full.
    monkeypatch.setenv('FLY_MACHINE_ID', 'machinea')
    relay = BrowserRelay(OWNER)
    threads = []

    def lookup(owner, device):
        threads.append(threading.get_ident())
        return None

    monkeypatch.setattr(device_routes, 'lookup', lookup)

    async def run():
        loop_thread = threading.get_ident()
        with pytest.raises(ChromeReadError):
            await relay.read('marketplace.sessions', {}, 1)
        return loop_thread

    loop_thread = asyncio.run(run())
    assert threads and loop_thread not in threads


def test_health_fails_when_the_draft_loop_has_stopped(monkeypatch):
    from concurrent.futures import Future

    from fastapi.testclient import TestClient

    monkeypatch.setenv('ANTEROOM_BACKGROUND_WORKER', '1')
    monkeypatch.setenv('ANTEROOM_PROCESS_ROLE', 'worker')
    app = create_app()
    finished = Future()
    finished.set_result(None)
    client = TestClient(app)
    app.state.background_worker = finished
    response = client.get('/api/health')
    assert response.status_code == 503 and response.json()['status'] == 'worker_stopped'
    app.state.background_worker = Future()
    assert client.get('/api/health').status_code == 200


def test_confirmed_device_route_is_cached_and_cleared_on_handoff_release(storage, monkeypatch):  # noqa: F811
    from sqlalchemy import event

    from anteroom.services import process_handoff

    engine, _sessions = storage
    owner = str(uuid.uuid4())
    monkeypatch.setenv('FLY_MACHINE_ID', 'machinecachea')
    monkeypatch.setenv('ANTEROOM_PROCESS_BASE_URL', 'http://127.0.0.1:8080')
    device_routes.clear_route_cache()

    queries: list[str] = []

    def record(_conn, _cursor, statement, *_args):
        queries.append(statement)

    event.listen(engine, 'before_cursor_execute', record)
    try:
        first = claim(owner, 'chrome', engine)
        assert first.owned and first.machine_id == 'machinecachea'
        assert queries

        queries.clear()
        second = claim(owner, 'chrome', engine)
        assert second == first
        assert queries == [], 'Confirmed owned route within TTL should skip the DB'

        found = device_routes.lookup(owner, 'chrome', engine)
        assert found == first
        assert queries == [], 'Lookup of confirmed owned route within TTL should skip the DB'

        # Releasing the handoff clears the route cache so the next claim hits DB.
        process_handoff.release(engine)
        queries.clear()
        refreshed = claim(owner, 'chrome', engine)
        assert refreshed == first
        assert queries, 'Handoff release must invalidate cached device routes'

        # A non-owned route is never cached.
        monkeypatch.setenv('FLY_MACHINE_ID', 'machinecacheb')
        monkeypatch.setenv('ANTEROOM_PROCESS_BASE_URL', 'http://127.0.0.1:8081')
        queries.clear()
        foreign = claim(owner, 'chrome', engine)
        assert not foreign.owned and foreign.machine_id == 'machinecachea'
        assert queries
        queries.clear()
        foreign_again = claim(owner, 'chrome', engine)
        assert not foreign_again.owned
        assert queries, 'Non-owned device routes must never be cached'
    finally:
        event.remove(engine, 'before_cursor_execute', record)
        device_routes.clear_route_cache()



def test_lookup_does_not_postpone_heartbeat_and_release_fences_late_fill(storage, monkeypatch):  # noqa: F811
    from anteroom.services import process_handoff

    engine, _ = storage
    owner = str(uuid.uuid4())
    monkeypatch.setenv('FLY_MACHINE_ID', 'blue')
    monkeypatch.setenv('ANTEROOM_PROCESS_BASE_URL', 'http://127.0.0.1:8080')
    assert claim(owner, 'chrome', engine).owned
    device_routes.clear_route_cache()
    assert device_routes.lookup(owner, 'chrome', engine).owned
    store = device_routes._store_owned_route

    def release_before_fill(*args):
        process_handoff.release(engine)
        store(*args)

    monkeypatch.setattr(device_routes, '_store_owned_route', release_before_fill)
    assert claim(owner, 'chrome', engine).owned
    # A cached lookup would skip the renewal (and the hook); a stale fill
    # would hide the release. In either case this would still appear owned.
    assert device_routes.lookup(owner, 'chrome', engine) is None
