"""The web's Chrome relay: a command reaches only its own seller's tab, and only its reply resolves it."""
import asyncio
import threading
import time
from types import SimpleNamespace

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.services import browser_bridge, hosted_browser_relay
from vendoo_studio.services.extension_connection import MARKETPLACE_ACTIONS, current_extension, extension_manager
from vendoo_studio.services.hosted_context import for_seller
from vendoo_studio.services.service_context import use


def test_relay_is_per_seller_and_refuses_other_commands():
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


def _bridge_accepts(command: dict) -> bool:
    """Whether Anteroom Bridge's own contract check takes this command as the page relays it."""
    import json
    import subprocess
    from pathlib import Path

    protocol = Path(__file__).resolve().parents[3] / 'vendoo-extension' / 'cloud-marketplace-protocol.js'
    script = f"console.log(JSON.stringify(require({json.dumps(str(protocol))}).command({json.dumps(command)}) !== null))"
    return json.loads(subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True).stdout)


def test_relayed_commands_are_ones_the_bridge_accepts():
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
