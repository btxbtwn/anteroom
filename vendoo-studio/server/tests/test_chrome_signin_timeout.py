"""Controlled relay fixtures: no browser, marketplace, credentials or seller data."""
import asyncio

import pytest

from vendoo_studio.services.hosted_browser_relay import BrowserRelay, ChromeReadError, MARKETPLACE_SESSIONS


@pytest.mark.parametrize('cancelled', [False, True])
def test_abandoned_signin_is_not_delivered_when_chrome_resumes(cancelled):
    async def scenario():
        relay = BrowserRelay()
        relay.note_seen({MARKETPLACE_SESSIONS})
        abandoned = asyncio.create_task(relay.read_local('marketplace.connect', {'marketplace': 'depop'}, 0.01))
        await asyncio.sleep(0)
        stale_id = relay._outbox[0]['message_id']
        if cancelled:
            abandoned.cancel()
        with pytest.raises(asyncio.CancelledError if cancelled else ChromeReadError):
            await abandoned
        assert not relay.is_waiting(stale_id)

        retry = asyncio.create_task(relay.read_local('marketplace.connect', {'marketplace': 'depop'}, 1))
        command = await relay.next_message({MARKETPLACE_SESSIONS}, 1)
        try:
            assert command['message_id'] != stale_id
            assert command['payload']['marketplace'] == 'depop'
            relay.resolve_wait(command['message_id'], {'ok': True, 'signed_in': True})
            assert await retry == {'ok': True, 'signed_in': True}
            assert await relay.next_message({MARKETPLACE_SESSIONS}, 0.01) is None
        finally:
            if not retry.done():
                retry.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await retry

    asyncio.run(scenario())


def test_abandoning_one_signin_preserves_other_pending_requests():
    async def scenario():
        relay = BrowserRelay()
        relay.note_seen({MARKETPLACE_SESSIONS})
        abandoned = asyncio.create_task(relay.read_local('marketplace.connect', {'marketplace': 'depop'}, 0.01))
        other = asyncio.create_task(relay.read_local('marketplace.connect', {'marketplace': 'poshmark'}, 1))
        with pytest.raises(ChromeReadError):
            await abandoned
        command = await relay.next_message({MARKETPLACE_SESSIONS}, 1)
        assert command['payload']['marketplace'] == 'poshmark'
        relay.resolve_wait(command['message_id'], {'ok': True})
        assert await other == {'ok': True}
        assert await relay.next_message({MARKETPLACE_SESSIONS}, 0.01) is None

    asyncio.run(scenario())
