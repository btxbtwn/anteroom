"""Hosted promotions retain seller approval, ownership and at-most-once sends."""
import pytest

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_hosted_browser_relay import _answer, _bridge_accepts, _in_background
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from anteroom.services import hosted_browser_relay

CAPABLE = 'marketplace.actions,marketplace.promotions'
POST = 'b' * 24


def test_automations_are_saved_off_and_seller_owned(cloud, signing_keys):  # noqa: F811
    client, _ = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    assert client.get('/api/promotion-automations').status_code == 401
    assert client.get('/api/promotion-automations', headers=alice).status_code == 200
    created = client.post('/api/promotion-automations', headers=alice, json={
        'marketplace': 'poshmark', 'action': 'share', 'daily_cap': 100, 'times': ['09:00'], 'timezone': 'UTC',
    })
    assert created.status_code == 200, created.text
    row = created.json()
    assert row['enabled'] is False
    assert client.get('/api/promotion-automations', headers=bob).json()['automations'] == []
    assert client.post(f"/api/promotion-automations/{row['id']}/enable", headers=bob, json={'confirm': True}).status_code == 404
    assert client.post(f"/api/promotion-automations/{row['id']}/enable", headers=alice, json={}).status_code == 422


def test_review_then_confirmed_share_reaches_only_own_chrome_once(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, _ = cloud
    monkeypatch.setattr('anteroom.routes.hosted_browser_relay.WAIT_S', 0.1)
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    hosted_browser_relay._relays.clear()
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Jacket'}).json()['id']
    assert client.post(f'/api/conversations/{identity}/marketplace-listings/poshmark/mark', headers=alice,
                       json={'state': 'listed', 'url': f'https://poshmark.com/listing/Jacket-{POST}'}).status_code == 200
    body = {'conversation_ids': [identity], 'marketplace': 'poshmark', 'action': 'share'}
    assert client.get('/api/promotions', headers=bob).json()['history'] == []
    refused = client.post('/api/promotions/preview', headers=alice, json=body)
    assert refused.status_code == 409 and 'Chrome' in refused.json()['detail']
    client.get('/api/browser-relay/next?capabilities=marketplace.actions', headers=alice)
    assert '0.2.52' in client.post('/api/promotions/preview', headers=alice, json=body).json()['detail']
    client.get('/api/browser-relay/next?capabilities=' + CAPABLE, headers=alice)
    thread, reviewed = _in_background(lambda: client.post('/api/promotions/preview', headers=alice, json=body))
    command = _answer(client, alice, {'ok': True, 'price_cents': 4500, 'like_count': 2}, CAPABLE)
    thread.join(10)
    assert _bridge_accepts(command), command
    assert command['type'] == 'marketplace.promotion_check'
    assert reviewed['response'].status_code == 200, reviewed['response'].text
    row = reviewed['response'].json()['rows'][0]
    url = f"/api/promotions/{row['id']}/perform"
    assert client.post(url, headers=alice, json={}).status_code == 422
    assert client.post(url, headers=bob, json={'confirm': True}).status_code == 404
    thread, sent = _in_background(lambda: client.post(url, headers=alice, json={'confirm': True}))
    command = _answer(client, alice, {'ok': True, 'state': 'done'}, CAPABLE)
    thread.join(10)
    assert command['type'] == 'marketplace.promote' and command['payload']['confirm'] is True
    assert _bridge_accepts(command), command
    assert sent['response'].status_code == 200, sent['response'].text
    assert sent['response'].json()['state'] == 'done'
    assert client.post(url, headers=alice, json={'confirm': True}).status_code == 409


@pytest.mark.parametrize('reply,expected', [({'ok': True, 'state': 'done'}, 'done'), ({}, 'unknown')])
def test_enabled_schedule_uses_own_chrome_and_keeps_desktop_limits(cloud, signing_keys, monkeypatch, reply, expected):  # noqa: F811
    import asyncio
    from datetime import datetime, timedelta
    from anteroom.database import SessionLocal
    from anteroom.models.promotion_automation import PromotionAutomation
    from anteroom.services import hosted_promotion_scheduler
    from anteroom.services.hosted_context import for_seller
    from anteroom.services.service_context import use

    client, _ = cloud
    monkeypatch.setattr('anteroom.routes.hosted_browser_relay.WAIT_S', 0.1)
    alice = headers(signing_keys)
    hosted_browser_relay._relays.clear()
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Coat'}).json()['id']
    client.post(f'/api/conversations/{identity}/marketplace-listings/poshmark/mark', headers=alice,
                json={'state': 'listed', 'url': f'https://poshmark.com/listing/Coat-{POST}'})
    created = client.post('/api/promotion-automations', headers=alice, json={
        'marketplace': 'poshmark', 'action': 'share', 'daily_cap': 1, 'times': ['09:00'], 'timezone': 'UTC',
        'scope': 'items', 'conversation_ids': [identity],
    }).json()
    assert created['enabled'] is False
    enabled = client.post(f"/api/promotion-automations/{created['id']}/enable", headers=alice, json={'confirm': True})
    assert enabled.status_code == 200 and enabled.json()['enabled'] is True
    assert hosted_browser_relay.ready_sellers(hosted_browser_relay.MARKETPLACE_PROMOTIONS) == []
    client.get('/api/browser-relay/next?capabilities=' + CAPABLE, headers=alice)
    with use(for_seller(USER_ID)), SessionLocal() as db:
        db.get(PromotionAutomation, created['id']).next_run_at = datetime.now() - timedelta(minutes=1)
        db.commit()

    recovered = set()
    thread, result = _in_background(lambda: asyncio.run(hosted_promotion_scheduler.check_seller(USER_ID, recovered)))
    read = _answer(client, alice, {'ok': True, 'price_cents': 4500, 'like_count': 2}, CAPABLE)
    try:
        send = _answer(client, alice, reply, CAPABLE)
    except AssertionError:
        raise AssertionError(client.get(f"/api/promotion-automations/{created['id']}/activity", headers=alice).json()) from None
    thread.join(10)
    assert not thread.is_alive() and 'response' in result
    assert _bridge_accepts(read) and _bridge_accepts(send)
    assert send['type'] == 'marketplace.promote' and send['payload']['confirm'] is True
    assert send['payload']['gap_ms'] >= 4000
    assert recovered == {USER_ID}
    activity = client.get(f"/api/promotion-automations/{created['id']}/activity", headers=alice).json()
    assert activity['runs'][0]['sent'] == (1 if expected == 'done' else 0)
    assert activity['sends'][0]['state'] == expected
    if expected == 'unknown':
        saved = client.get('/api/promotion-automations', headers=alice).json()['automations'][0]
        assert saved['enabled'] is False
        asyncio.run(hosted_promotion_scheduler.check_seller(USER_ID, recovered))
        assert client.get('/api/browser-relay/next?capabilities=' + CAPABLE, headers=alice).status_code == 204


def test_timer_only_checks_capable_connected_sellers(monkeypatch):
    import asyncio
    import time
    from unittest.mock import AsyncMock
    from anteroom.services import hosted_promotion_scheduler

    hosted_browser_relay._relays.clear()
    for owner, capabilities in ((USER_ID, {'marketplace.actions', 'marketplace.promotions'}), (BOB, {'marketplace.actions'})):
        relay = hosted_browser_relay.relay_for(owner)
        relay._seen_at, relay._capabilities = time.monotonic(), frozenset(capabilities)
    check = AsyncMock()
    monkeypatch.setattr(hosted_promotion_scheduler, 'check_seller', check)
    recovered = set()
    asyncio.run(hosted_promotion_scheduler.check_ready_sellers(recovered))
    check.assert_awaited_once_with(USER_ID, recovered)
    hosted_browser_relay._relays.clear()


def test_disconnected_seller_is_monitored_without_running_a_round(monkeypatch):
    import asyncio
    from contextlib import nullcontext
    from unittest.mock import AsyncMock, Mock
    from anteroom.services import hosted_promotion_scheduler as scheduler

    db = object()
    report = Mock()
    run = AsyncMock()
    monkeypatch.setattr(scheduler, 'ready_sellers', lambda _: [])
    monkeypatch.setattr(scheduler, 'for_seller', lambda owner: owner)
    monkeypatch.setattr(scheduler, 'use', nullcontext)
    monkeypatch.setattr(scheduler, 'SessionLocal', lambda: nullcontext(db))
    monkeypatch.setattr(scheduler.promotion_automations, 'report_overdue', report)
    monkeypatch.setattr(scheduler, 'check_seller', run)
    asyncio.run(scheduler.check_ready_sellers({USER_ID}))
    report.assert_called_once()
    assert report.call_args.args == (db,)
    assert report.call_args.kwargs["reason"] == "chrome_disconnected"
    run.assert_not_awaited()


def test_generic_read_cannot_send_a_promotion():
    import asyncio
    relay = hosted_browser_relay.BrowserRelay()
    with pytest.raises(ValueError, match='not a relayed read'):
        asyncio.run(relay.read('marketplace.promote', {'confirm': True}, 1))



def test_mercari_preview_cooldown_is_seller_scoped(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, _ = cloud
    hosted_browser_relay._relays.clear()
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    monkeypatch.setattr('anteroom.routes.hosted_browser_relay.WAIT_S', 0.1)
    bodies = []
    for seller in (alice, bob):
        client.get('/api/browser-relay/next?capabilities=' + CAPABLE, headers=seller)
        identity = client.post('/api/conversations', headers=seller, json={'title': 'Coat'}).json()['id']
        marked = client.post(f'/api/conversations/{identity}/marketplace-listings/mercari/mark', headers=seller,
                            json={'state': 'listed', 'url': 'https://www.mercari.com/us/item/m82647727742/'})
        assert marked.status_code == 200, marked.text
        bodies.append({'conversation_ids': [identity], 'marketplace': 'mercari', 'action': 'offer',
                       'discount_percent': 12, 'floors': {identity: '1'}})
    thread, first = _in_background(lambda: client.post('/api/promotions/preview', headers=alice, json=bodies[0]))
    _answer(client, alice, {'ok': False, 'error': 'Mercari refused the request.', 'error_code': 'rejected',
                           'safe_to_retry': True,
                           'marketplace_message': 'You can make an offer again in 47 hours. (once every 3 days)'}, CAPABLE)
    thread.join(10)
    assert not thread.is_alive() and first['response'].status_code == 200
    assert first['response'].json()['rows'][0]['state'] == 'skipped'
    again = client.post('/api/promotions/preview', headers=alice, json=bodies[0])
    assert again.status_code == 200 and again.json()['rows'][0]['state'] == 'skipped'
    # The same remote item ID in another seller's account must still be reviewed.
    thread, second = _in_background(lambda: client.post('/api/promotions/preview', headers=bob, json=bodies[1]))
    command = _answer(client, bob, {'ok': True, 'price_cents': 1000, 'like_count': 2, 'offer_price_cents': 800}, CAPABLE)
    thread.join(10)
    assert command['type'] == 'marketplace.promotion_check'
    assert not thread.is_alive() and second['response'].status_code == 200
    assert second['response'].json()['rows'][0]['state'] == 'preview'


def test_promotion_scheduler_bounds_database_steps_without_blocking_sellers(monkeypatch):
    import asyncio
    import threading
    from contextlib import nullcontext

    from anteroom import database
    from anteroom.services import hosted_promotion_scheduler as scheduler

    sellers = {'alice', 'bob', 'charlie'}
    monkeypatch.setattr(scheduler, 'ready_sellers', lambda _: sellers)
    monkeypatch.setattr(database, 'SessionLocal', lambda: nullcontext())

    async def run():
        loop = asyncio.get_running_loop()
        db_busy = asyncio.Event()
        release_db = threading.Event()
        browser = asyncio.Event()
        release_browser = asyncio.Event()
        lock = threading.Lock()
        active = peak = 0
        entered_browser = set()

        def db_step(_db):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == 2:
                    loop.call_soon_threadsafe(db_busy.set)
            try:
                assert release_db.wait(5)
            finally:
                with lock:
                    active -= 1

        async def check(owner, _recovered):
            await database.in_worker_session(db_step)
            entered_browser.add(owner)
            if entered_browser == sellers:
                browser.set()
            await release_browser.wait()

        monkeypatch.setattr(scheduler, 'check_seller', check)
        wave = asyncio.create_task(scheduler.check_ready_sellers(set()))
        try:
            await asyncio.wait_for(db_busy.wait(), 3)
            release_db.set()
            await asyncio.wait_for(browser.wait(), 3)
            assert peak == 2
        finally:
            release_db.set()
            release_browser.set()
            await asyncio.wait_for(wave, 5)

    asyncio.run(run())


def test_cloud_like_checks_pace_to_interval_while_scheduled_ticks_still_run(monkeypatch):
    import asyncio
    from contextlib import nullcontext
    from unittest.mock import AsyncMock
    from anteroom.services import cloud_sessions, hosted_promotion_scheduler as scheduler, promotion_automations

    clock = [1000.0]
    draws = iter([45.0, 360.0, 270.0, 330.0])
    monkeypatch.setattr(scheduler.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(scheduler.random, 'uniform', lambda low, high: next(draws))
    monkeypatch.setattr(scheduler, 'for_seller', lambda owner: owner)
    monkeypatch.setattr(scheduler, 'use', lambda _ctx: nullcontext())
    monkeypatch.setattr(scheduler, 'in_worker_session', AsyncMock())
    active_owners = [USER_ID]
    monkeypatch.setattr(cloud_sessions, 'active_owners', lambda _engine, market: active_owners if market == 'poshmark' else [])
    likes = AsyncMock()
    tick = AsyncMock()
    monkeypatch.setattr(promotion_automations, 'check_new_likes', likes)
    monkeypatch.setattr(promotion_automations, 'tick', tick)

    recovered: set[str] = set()
    like_checks: dict[tuple[str, str], float] = {}

    # Initial tick staggers the first like check by 45s while still running scheduled ticks.
    asyncio.run(scheduler.check_cloud_sellers(object(), recovered, like_checks, startup=True))
    assert likes.await_count == 0
    assert tick.await_count == 1
    assert like_checks[(USER_ID, 'poshmark')] == 1045.0

    # Once the stagger elapses, the like check runs and arms the next jittered due time (+360s).
    clock[0] += 60.0
    asyncio.run(scheduler.check_cloud_sellers(object(), recovered, like_checks))
    assert likes.await_count == 1
    assert tick.await_count == 2
    assert like_checks[(USER_ID, 'poshmark')] == 1420.0

    # Before the jittered due time: like check is skipped, scheduled tick still runs.
    clock[0] += 300.0
    asyncio.run(scheduler.check_cloud_sellers(object(), recovered, like_checks))
    assert likes.await_count == 1
    assert tick.await_count == 3

    # After the jittered due time elapses: like check runs again.
    clock[0] += 60.0
    asyncio.run(scheduler.check_cloud_sellers(object(), recovered, like_checks))
    assert likes.await_count == 2
    assert tick.await_count == 4
    assert like_checks[(USER_ID, 'poshmark')] == 1690.0

    # An observed disconnect clears the deadline. Reconnecting checks likes
    # on the next round even though the old deadline would still be in the future.
    active_owners.clear()
    asyncio.run(scheduler.check_cloud_sellers(object(), recovered, like_checks))
    assert like_checks == {}
    active_owners.append(USER_ID)
    clock[0] += 60.0
    asyncio.run(scheduler.check_cloud_sellers(object(), recovered, like_checks))
    assert likes.await_count == 3
    assert tick.await_count == 5
    assert like_checks[(USER_ID, 'poshmark')] == 1810.0

