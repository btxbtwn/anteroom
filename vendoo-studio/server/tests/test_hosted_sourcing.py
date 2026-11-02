"""Sourcing on the web: each seller's preferences and buy list are their own, never the Mac's file."""
import asyncio
from datetime import timedelta
import threading
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.services import box_scout, hosted_sourcing_scheduler
from vendoo_studio.services.hosted_context import for_seller
from vendoo_studio.services.hosted_database import SellerDatabaseSession
from vendoo_studio.services.service_context import use

# Collected before conftest swaps the timer out for the whole session.
REAL_SERVE = hosted_sourcing_scheduler.serve


def test_sourcing_is_private_to_each_seller(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, _ = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    monkeypatch.setattr(box_scout, 'state_path', lambda: (_ for _ in ()).throw(AssertionError('read the Mac sourcing file')))
    refreshed, done = [], threading.Event()

    def fake_refresh(*, recrawl, research):
        # Runs on the refresh thread: it must still be the seller who asked.
        refreshed.append(box_scout._seller())
        done.set()

    monkeypatch.setattr(box_scout, '_refresh', fake_refresh)
    assert 'sourcing' in client.get('/api/runtime', headers=alice).json()['views']
    saved = client.put('/api/sourcing/prefs', headers=alice, json={'zip': '10001', 'budget': 500})
    assert saved.status_code == 200, saved.text
    assert saved.json()['prefs']['zip'] == '10001'
    assert done.wait(5) and refreshed[0] == USER_ID
    assert client.get('/api/sourcing', headers=alice).json()['prefs']['budget'] == 500
    bob_state = client.get('/api/sourcing', headers=bob).json()
    assert bob_state['prefs']['zip'] == box_scout.DEFAULT_PREFS['zip']
    assert bob_state['prefs']['budget'] == box_scout.DEFAULT_PREFS['budget']


def save_sourcing(owner, *, age=None, snapshot_format=box_scout.SNAPSHOT_FORMAT, **changed_prefs):
    """Save a seller's sourcing state with a buy list built `age` ago, then change their plan."""
    with use(for_seller(owner)):
        state = box_scout.read_state()
        if age is not None:
            state['snapshot'] = {'format': snapshot_format, 'preferences': dict(state['prefs']),
                                 'updated_at': (box_scout._now() - age).isoformat(), 'boxes': []}
        state['prefs'].update(changed_prefs)
        box_scout._write_state(state)


def test_only_the_trusted_login_lists_sellers_whose_sourcing_refresh_is_due(cloud):  # noqa: F811
    _client, engine = cloud
    sellers = {name: str(uuid.uuid4()) for name in
               ('fresh', 'stale', 'new_plan', 'no_buy_list', 'old_format', 'future', 'never_opened')}
    save_sourcing(sellers['fresh'], age=timedelta(hours=1))
    save_sourcing(sellers['stale'], age=box_scout.REFRESH_INTERVAL + timedelta(minutes=1))
    save_sourcing(sellers['new_plan'], age=timedelta(hours=1), zip='10001')
    save_sourcing(sellers['no_buy_list'])
    save_sourcing(sellers['old_format'], age=timedelta(hours=1), snapshot_format=box_scout.SNAPSHOT_FORMAT - 1)
    save_sourcing(sellers['future'], age=-timedelta(hours=1))

    due = set(hosted_sourcing_scheduler.due_owners(engine))
    assert due == {sellers[name] for name in ('stale', 'new_plan', 'no_buy_list', 'old_format', 'future')}
    # The list follows the Mac timer's own rule for every seller who has used Sourcing.
    for name, owner in sellers.items():
        if name != 'never_opened':
            with use(for_seller(owner)):
                assert box_scout.refresh_is_due() == (owner in due), name

    # A seller's own session (the role HTTP handlers use) cannot call it.
    with SellerDatabaseSession(user_id=sellers['stale'], bind=engine) as db, pytest.raises(DBAPIError):
        db.execute(text("SELECT * FROM anteroom_private.due_sourcing_owners(5, ARRAY['zip'], '', '')")).all()


def test_web_timer_refreshes_each_due_seller_in_their_own_context(cloud, monkeypatch):  # noqa: F811
    _client, engine = cloud
    broken, carol = str(uuid.uuid4()), str(uuid.uuid4())
    save_sourcing(USER_ID, age=box_scout.REFRESH_INTERVAL + timedelta(hours=1), zip='10001')
    save_sourcing(BOB, age=timedelta(hours=1))
    save_sourcing(broken, age=box_scout.REFRESH_INTERVAL + timedelta(hours=1))
    save_sourcing(carol, age=box_scout.REFRESH_INTERVAL + timedelta(hours=1), zip='60601')
    refreshed = []

    def fake_refresh(*, recrawl, research):
        seller = box_scout._seller()
        if seller == broken:
            raise RuntimeError('store is down')
        state = box_scout.read_state()
        refreshed.append((seller, state['prefs']['zip']))
        state['snapshot'] = {'format': box_scout.SNAPSHOT_FORMAT, 'preferences': dict(state['prefs']),
                             'updated_at': box_scout._now().isoformat(), 'boxes': []}
        box_scout._write_state(state)

    class OnePass(Exception):
        pass

    async def stop(_seconds):
        raise OnePass

    monkeypatch.setattr(box_scout, '_refresh', fake_refresh)
    monkeypatch.setattr(hosted_sourcing_scheduler.asyncio, 'sleep', stop)
    # The real serve; conftest swaps it out so app startup never refreshes in tests.
    with pytest.raises(OnePass):
        asyncio.run(REAL_SERVE(engine))

    # One seller's failure did not stop the others; Bob's fresh list was left alone.
    assert sorted(refreshed) == sorted([(USER_ID, '10001'), (carol, '60601')])
    assert set(hosted_sourcing_scheduler.due_owners(engine)) == {broken}
    with use(for_seller(BOB)):
        assert box_scout.read_state()['prefs']['zip'] == box_scout.DEFAULT_PREFS['zip']


def test_web_timer_skips_a_seller_whose_refresh_is_already_running(cloud, monkeypatch):  # noqa: F811
    save_sourcing(USER_ID, age=box_scout.REFRESH_INTERVAL + timedelta(hours=1))
    monkeypatch.setattr(box_scout, '_refresh', lambda **_: pytest.fail('refreshed twice at once'))
    with use(for_seller(USER_ID)):
        lock = box_scout._refresh_lock()
    assert lock.acquire(blocking=False)
    try:
        assert hosted_sourcing_scheduler.refresh_owner(USER_ID) is False
    finally:
        lock.release()
