"""The web's Chrome relay: a command reaches only its own seller's tab, and only its reply resolves it."""
import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from anteroom.services import browser_bridge, hosted_browser_relay, listing_mode
from anteroom.services.extension_connection import MARKETPLACE_ACTIONS, MARKETPLACE_REGIONS, current_extension, extension_manager
from anteroom.services.hosted_context import for_seller
from anteroom.services.service_context import use


@pytest.fixture
def direct_mode(monkeypatch):
    # Transport-only tests have no Postgres fixture. Mode persistence and
    # ownership are exercised through the real hosted routes in test_hosted_vendoo_mode.
    check = Mock()
    monkeypatch.setattr(listing_mode, 'require', check)
    return check


def test_old_bridge_can_poll_past_regional_command_without_consuming_it():
    async def scenario():
        relay = hosted_browser_relay.BrowserRelay(USER_ID)
        current_caps = {MARKETPLACE_ACTIONS, MARKETPLACE_REGIONS}
        relay.note_seen(current_caps)
        regional = {"type": "marketplace.delete", "job_id": "job1", "message_id": "one",
                    "payload": {"marketplace": "poshmark", "listing_id": "a" * 24,
                                "seller_region": "CA", "confirm": True}}
        local = {**regional, "message_id": "two", "payload": {"marketplace": "poshmark"}}
        assert await relay.deliver_local(regional)
        assert await relay.deliver_local(local)
        old_command = await relay.next_message({MARKETPLACE_ACTIONS}, 0)
        assert old_command is not None and old_command["message_id"] == "two"
        assert await relay.next_message({MARKETPLACE_ACTIONS}, 0.01) is None
        new_command = await relay.next_message(current_caps, 0)
        assert new_command is not None and new_command["message_id"] == "one"

    asyncio.run(scenario())


def test_regional_request_timeout_removes_command_skipped_by_old_bridge():
    async def scenario():
        relay = hosted_browser_relay.BrowserRelay(USER_ID)
        current_caps = {MARKETPLACE_ACTIONS, MARKETPLACE_REGIONS}
        relay.note_seen(current_caps)
        request = asyncio.create_task(relay.request_local(
            {"type": "marketplace.delete", "job_id": "job1", "message_id": "one",
             "payload": {"marketplace": "poshmark", "listing_id": "a" * 24,
                         "seller_region": "CA", "confirm": True}}, timeout=0.05))
        await asyncio.sleep(0)
        assert await relay.next_message({MARKETPLACE_ACTIONS}, 0) is None
        with pytest.raises(hosted_browser_relay.ChromeReadError, match="did not answer in time"):
            await request
        assert await relay.next_message(current_caps, 0) is None

    asyncio.run(scenario())


def test_relay_is_per_seller_and_refuses_other_commands(direct_mode):
    hosted_browser_relay._relays.clear()

    async def scenario():
        with use(for_seller(USER_ID)):
            relay = current_extension()
            assert relay is not extension_manager
            # No tab has asked yet: Chrome is not connected, and nothing is queued.
            assert not relay.connected and 'Open Anteroom in Chrome' in relay.refusal()
            # A tab that declared nothing is not connected yet; one that
            # declared some reads but not marketplace actions needs a newer Bridge.
            assert await relay.next_message(set(), 0.01) is None
            assert 'Open Anteroom in Chrome' in relay.refusal()
            assert await relay.next_message({hosted_browser_relay.SALES_HISTORY}, 0.01) is None
            assert relay.refusal().startswith('Update Anteroom Bridge')
            assert await relay.next_message({MARKETPLACE_ACTIONS}, 0.01) is None
            assert relay.connected and relay.refusal() is None
            assert not await relay.send_message({'type': 'browser.open'})
            job = SimpleNamespace(id='job1')
            request = asyncio.create_task(browser_bridge.request(job, 'marketplace.execute', {'operation_id': 'op'}, timeout=5))
            message = await relay.next_message({MARKETPLACE_ACTIONS}, 1)
            assert message['type'] == 'marketplace.execute' and message['payload']['operation_id'] == 'op'
        with use(for_seller(BOB)):
            bob = current_extension()
            assert await bob.next_message({MARKETPLACE_ACTIONS}, 0.01) is None
            assert not bob.is_waiting(message['message_id'])
        relay.resolve_wait(message['message_id'], {'ok': True, 'state': 'draft'})
        assert await request == {'ok': True, 'state': 'draft'}

    asyncio.run(scenario())
    direct_mode.assert_called_once_with('direct')


@pytest.mark.parametrize('capability', sorted(hosted_browser_relay.NEEDS))
def test_a_relay_no_tab_has_polled_asks_to_connect_even_on_a_freshly_booted_machine(monkeypatch, capability):
    # Monotonic time starts near zero when a Machine boots. A relay nobody has
    # polled must not count as seen during its first SEEN_FOR_S seconds, or a
    # deploy tells the seller to update a Bridge that simply has not reconnected.
    monkeypatch.setattr(hosted_browser_relay, 'time', SimpleNamespace(monotonic=lambda: 30.0))
    relay = hosted_browser_relay.BrowserRelay()
    assert not relay._can(capability)
    assert relay._local_refusal(capability) == 'Open Anteroom in Chrome on your computer and connect Chrome first.'
    # A polled tab that declared no capabilities is not connected yet either.
    relay.note_seen(set())
    assert relay._local_refusal(capability) == 'Open Anteroom in Chrome on your computer and connect Chrome first.'


def test_relayed_reads_ask_to_connect_until_a_tab_declares_capabilities(monkeypatch):
    monkeypatch.setattr(hosted_browser_relay, 'time', SimpleNamespace(monotonic=lambda: 30.0))
    relay = hosted_browser_relay.BrowserRelay()

    async def read():
        with pytest.raises(hosted_browser_relay.ChromeReadError) as error:
            await relay.read_local('marketplace.sessions', {}, 1)
        return str(error.value)

    assert asyncio.run(read()) == 'Open Anteroom in Chrome on your computer and connect Chrome first.'
    # Declared, but without the read it needs: that Bridge is out of date.
    relay.note_seen({MARKETPLACE_ACTIONS})
    assert relay._local_refusal(hosted_browser_relay.MARKETPLACE_SESSIONS) == (
        'Update Anteroom Bridge in Chrome to 0.2.44 or later to check marketplace sign-in from the web.')
    assert asyncio.run(read()).startswith('Update Anteroom Bridge in Chrome to 0.2.44')


def test_heartbeat_keeps_a_busy_reader_connected_and_expires_after_tab_loss(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(hosted_browser_relay, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    relay = hosted_browser_relay.BrowserRelay()
    relay.note_seen({MARKETPLACE_ACTIONS, hosted_browser_relay.SALES_HISTORY})
    # A command can take minutes; the tab sends heartbeats while waiting for its reply.
    for _ in range(6):
        clock[0] += 20
        relay.note_seen({MARKETPLACE_ACTIONS, hosted_browser_relay.SALES_HISTORY})
    assert relay.connected and relay.refusal(hosted_browser_relay.SALES_HISTORY) is None
    clock[0] += hosted_browser_relay.SEEN_FOR_S + 1
    assert not relay.connected


def test_heartbeat_is_authenticated_and_scoped_to_the_seller(cloud, signing_keys):  # noqa: F811
    client, _ = cloud
    hosted_browser_relay._relays.clear()
    body = {'capabilities': [MARKETPLACE_ACTIONS, hosted_browser_relay.SALES_HISTORY]}
    assert client.post('/api/browser-relay/heartbeat', json=body).status_code == 401
    assert client.post('/api/browser-relay/heartbeat', headers=headers(signing_keys), json=body).status_code == 200
    assert hosted_browser_relay.relay_for(USER_ID).connected
    assert not hosted_browser_relay.relay_for(BOB).connected
    assert client.post('/api/browser-relay/heartbeat', headers=headers(signing_keys),
                       json={'capabilities': ['browser.open']}).status_code == 422


def test_page_relays_commands_and_replies_for_its_own_seller(cloud, signing_keys):  # noqa: F811
    client, _ = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    hosted_browser_relay._relays.clear()
    assert client.get('/api/browser-relay/next').status_code == 401
    relay = hosted_browser_relay.relay_for(USER_ID)
    relay._seen_at, relay._capabilities = time.monotonic(), frozenset({MARKETPLACE_ACTIONS})
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        waiter = asyncio.run_coroutine_threadsafe(_send(relay), loop).result(5)
        command = client.get('/api/browser-relay/next?capabilities=marketplace.actions', headers=alice)
        assert command.status_code == 200 and command.json()['type'] == 'marketplace.delete'
        request_id = command.json()['message_id']
        assert client.post('/api/browser-relay/replies', headers=bob,
                           json={'request_id': request_id, 'payload': {'ok': True}}).status_code == 404
        assert client.post('/api/browser-relay/replies', headers=alice,
                           json={'request_id': request_id, 'payload': {'ok': True, 'state': 'deleted'}}).status_code == 200
        assert asyncio.run_coroutine_threadsafe(asyncio.wait_for(waiter, 5), loop).result(6) == {'ok': True, 'state': 'deleted'}
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
    assert client.get('/api/browser-relay/jobs/missing/photos/p1', headers=alice).status_code == 404


def test_a_replys_timing_is_kept_on_its_job(cloud, signing_keys):  # noqa: F811
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from anteroom.models.job import Job

    client, engine = cloud
    alice = headers(signing_keys)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Tee'}).json()['id']
    with Session(engine) as db:
        db.add(Job(id='job-timed', conversation_id=identity, approved_revision_id='rev', user_id=USER_ID,
                   listing_snapshot={}, status='dispatched', current_step='native_poshmark_list'))
        db.commit()
    hosted_browser_relay._relays.clear()
    relay = hosted_browser_relay.relay_for(USER_ID)
    relay._seen_at, relay._capabilities = time.monotonic(), frozenset({MARKETPLACE_ACTIONS})
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        async def send():
            waiter = relay.register_wait('b' * 32, 'job-timed')
            assert await relay.send_message({'type': 'marketplace.delete', 'job_id': 'job-timed', 'message_id': 'b' * 32,
                                             'payload': {'marketplace': 'poshmark', 'listing_id': 'x', 'confirm': True}})
            return waiter

        waiter = asyncio.run_coroutine_threadsafe(send(), loop).result(5)
        command = client.get('/api/browser-relay/next?capabilities=marketplace.actions', headers=alice).json()
        timing = {'photos_ms': 1200, 'photo_bytes': 9_000_000, 'chrome_ms': 45000}
        assert client.post('/api/browser-relay/replies', headers=alice, json={
            'request_id': command['message_id'], 'payload': {'ok': True, 'state': 'deleted'}, 'timing': timing}).status_code == 200
        asyncio.run_coroutine_threadsafe(asyncio.wait_for(waiter, 5), loop).result(6)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT event_type, step, payload FROM job_events WHERE job_id = 'job-timed'")).all()
    assert [(row.event_type, row.step) for row in rows] == [('relay_timing', 'marketplace.delete')]
    recorded = rows[0].payload
    assert {key: recorded[key] for key in timing} == timing
    assert recorded['queued_ms'] >= 0 and recorded['total_ms'] >= recorded['queued_ms']


async def _send(relay):
    request_id = 'a' * 32
    waiter = relay.register_wait(request_id, 'job1')
    assert await relay.send_message({'type': 'marketplace.delete', 'message_id': request_id,
                                     'payload': {'marketplace': 'poshmark', 'listing_id': 'x', 'confirm': True}})
    return waiter



def _answer(client, alice, reply, capabilities='marketplace.actions'):
    """Act as the seller's Anteroom tab: take the next command for their Chrome and post the Bridge's reply."""
    for _ in range(20):
        command = client.get('/api/browser-relay/next?capabilities=' + capabilities, headers=alice)
        if command.status_code == 200:
            body = command.json()
            assert client.post('/api/browser-relay/replies', headers=alice,
                               json={'request_id': body['message_id'], 'payload': reply}).status_code == 200
            return body
    raise AssertionError('No command reached the tab')


def _in_background(call):
    result = {}
    thread = threading.Thread(target=lambda: result.update(response=call()), daemon=True)
    thread.start()
    return thread, result


def _bridge_accepts(command: dict) -> bool:
    """Whether Anteroom Bridge's own contract check takes this command as the page relays it."""
    import json
    import subprocess
    from pathlib import Path

    protocol = Path(__file__).resolve().parents[3] / 'anteroom-extension' / 'cloud-marketplace-protocol.js'
    script = f"console.log(JSON.stringify(require({json.dumps(str(protocol))}).command({json.dumps(command)}) !== null))"
    return json.loads(subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True).stdout)


def test_relayed_commands_are_ones_the_bridge_accepts(direct_mode):
    from anteroom.services import native_marketplaces

    hosted_browser_relay._relays.clear()

    async def scenario():
        with use(for_seller(USER_ID)):
            relay = current_extension()
            await relay.next_message({MARKETPLACE_ACTIONS}, 0.01)
            request = asyncio.create_task(browser_bridge.request(SimpleNamespace(id='job-1'), 'marketplace.execute',
                                                                 {'marketplace': 'poshmark', 'action': 'list', 'confirm': True}, timeout=5))
            execute = await relay.next_message({MARKETPLACE_ACTIONS}, 1)
            relay.resolve_wait(execute['message_id'], {'ok': True})
            await request
            await native_marketplaces._send_cancel('op-1')
            cancel = await relay.next_message({MARKETPLACE_ACTIONS}, 1)
        return execute, cancel

    execute, cancel = asyncio.run(scenario())
    # The server's own envelope fields (version, sent_at) are not part of the page's contract.
    assert set(execute) == {'type', 'job_id', 'message_id', 'payload'}
    assert _bridge_accepts(execute)
    assert cancel['type'] == 'job.cancel' and cancel['payload']['job_id'] == 'op-1'
    assert _bridge_accepts(cancel)
    direct_mode.assert_called_once_with('direct')


def test_sales_history_uses_only_the_connected_sellers_capable_bridge():
    from anteroom.services.order_history import bridge_can_read

    hosted_browser_relay._relays.clear()
    capabilities = {MARKETPLACE_ACTIONS, hosted_browser_relay.SALES_HISTORY}

    async def scenario():
        with use(for_seller(USER_ID)):
            relay = current_extension()
            await relay.next_message({MARKETPLACE_ACTIONS}, 0.01)
            assert not bridge_can_read()
            await relay.next_message(capabilities, 0.01)
            assert bridge_can_read()
            request = asyncio.create_task(browser_bridge.request(SimpleNamespace(id=None), 'marketplace.sales_history',
                                                                 {'marketplace': 'depop', 'settled': ['receipt-1']}, timeout=5))
            command = await relay.next_message(capabilities, 1)
            assert _bridge_accepts(command)
            assert command['payload']['settled'] == ['receipt-1']
        with use(for_seller(BOB)):
            assert not current_extension().is_waiting(command['message_id'])
        relay.resolve_wait(command['message_id'], {'ok': True, 'orders': []})
        assert await request == {'ok': True, 'orders': []}

    asyncio.run(scenario())


@pytest.mark.parametrize('outcome', ['ok', 'sql_failure', 'unexpected', 'cancelled', 'pinned', 'missing', 'no_timing'])
def test_reply_diagnostics_preserve_await_order_errors_and_private_payloads(monkeypatch, outcome):
    from unittest.mock import MagicMock
    from sqlalchemy.exc import SQLAlchemyError
    from fastapi import HTTPException, Response
    from anteroom.routes import hosted_browser_relay as route
    from anteroom.services import request_timing

    details, events = {}, []
    token = request_timing._details.set(details)
    relay = MagicMock()
    relay.is_waiting.return_value = outcome != 'missing'
    relay.timing.return_value = None if outcome == 'no_timing' else {'job_id': 'private job', 'type': 'private operation'}
    relay.resolve_wait.side_effect = lambda *_: events.append('resolved')
    monkeypatch.setattr(route, '_relay', lambda: relay)

    def pinned(**_kwargs):
        events.append('lookup')
        return Response(status_code=409) if outcome == 'pinned' else None

    def persist(_timing):
        events.append('persist')
        if outcome == 'sql_failure':
            raise SQLAlchemyError('private storage detail')
        if outcome == 'unexpected':
            raise RuntimeError('private unexpected failure')
        if outcome == 'cancelled':
            raise asyncio.CancelledError()

    monkeypatch.setattr(route, '_pinned', pinned)
    monkeypatch.setattr(route, '_record_timing', persist)
    body = route.Reply(request_id='a' * 32, payload={'private': 'marketplace reply'})
    try:
        if outcome in ('unexpected', 'cancelled', 'missing'):
            error = {'unexpected': RuntimeError, 'cancelled': asyncio.CancelledError, 'missing': HTTPException}[outcome]
            with pytest.raises(error):
                asyncio.run(route.post_reply(body))
        else:
            result = asyncio.run(route.post_reply(body))
            assert (result.status_code if outcome == 'pinned' else result) == (409 if outcome == 'pinned' else {'ok': True})
        persisted = outcome not in ('pinned', 'missing', 'no_timing')
        assert details['relay_lookup_calls'] == 1
        assert details['relay_persist_calls'] == int(persisted)
        assert all(type(value) is int and value >= 0 for value in details.values())
        assert 'private' not in repr(details) and 'a' * 32 not in repr(details)
        expected = ['lookup'] + (['persist'] if persisted else [])
        if outcome in ('ok', 'sql_failure', 'no_timing'):
            expected += ['resolved']
        assert events == expected
    finally:
        request_timing._details.reset(token)


@pytest.mark.parametrize('blocked_step', ['lookup', 'persist'])
def test_reply_request_cancellation_does_not_resolve_while_its_worker_finishes(monkeypatch, blocked_step):
    from unittest.mock import MagicMock
    from anteroom.routes import hosted_browser_relay as route
    from anteroom.services import request_timing

    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    worker_released = []
    clock = [10.0]
    monkeypatch.setattr(request_timing, 'perf_counter', lambda: clock[0])
    details = {}
    token = request_timing._details.set(details)
    relay = MagicMock()
    relay.is_waiting.return_value = True
    relay.timing.return_value = {'job_id': 'private job', 'type': 'private operation'}
    monkeypatch.setattr(route, '_relay', lambda: relay)

    def block(step):
        if blocked_step == step:
            entered.set()
            try:
                worker_released.append(release.wait(3))
            finally:
                finished.set()

    def lookup(**_kwargs):
        block('lookup')
        return None

    def persist(_timing):
        block('persist')

    monkeypatch.setattr(route, '_pinned', lookup)
    monkeypatch.setattr(route, '_record_timing', persist)

    async def run():
        task = asyncio.create_task(route.post_reply(route.Reply(request_id='b' * 32, payload={'private': 'reply'})))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            clock[0] = 10.125
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert details[f'relay_{blocked_step}_calls'] == 1
            assert not finished.is_set()
            assert details[f'relay_{blocked_step}_ms'] == 125
            relay.resolve_wait.assert_not_called()
        finally:
            release.set()
            assert await asyncio.to_thread(finished.wait, 2)
            assert worker_released == [True]
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    try:
        asyncio.run(run())
        relay.resolve_wait.assert_not_called()
    finally:
        request_timing._details.reset(token)
