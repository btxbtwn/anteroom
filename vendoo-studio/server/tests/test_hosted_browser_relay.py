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
from vendoo_studio.services import browser_bridge, hosted_browser_relay, listing_mode
from vendoo_studio.services.extension_connection import MARKETPLACE_ACTIONS, current_extension, extension_manager
from vendoo_studio.services.hosted_context import for_seller
from vendoo_studio.services.service_context import use


@pytest.fixture
def direct_mode(monkeypatch):
    # Transport-only tests have no Postgres fixture. Mode persistence and
    # ownership are exercised through the real hosted routes in test_hosted_vendoo_mode.
    check = Mock()
    monkeypatch.setattr(listing_mode, 'require', check)
    return check


def test_relay_is_per_seller_and_refuses_other_commands(direct_mode):
    hosted_browser_relay._relays.clear()

    async def scenario():
        with use(for_seller(USER_ID)):
            relay = current_extension()
            assert relay is not extension_manager
            # No tab has asked yet: Chrome is not connected, and nothing is queued.
            assert not relay.connected and 'Open Anteroom in Chrome' in relay.refusal()
            assert await relay.next_message(set(), 0.01) is None
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

    protocol = Path(__file__).resolve().parents[3] / 'vendoo-extension' / 'cloud-marketplace-protocol.js'
    script = f"console.log(JSON.stringify(require({json.dumps(str(protocol))}).command({json.dumps(command)}) !== null))"
    return json.loads(subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True).stdout)


def test_relayed_commands_are_ones_the_bridge_accepts(direct_mode):
    from vendoo_studio.services import native_marketplaces

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
    from vendoo_studio.services.order_history import bridge_can_read

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
