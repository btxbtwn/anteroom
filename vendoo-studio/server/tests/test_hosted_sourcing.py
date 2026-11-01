"""Sourcing on the web: each seller's preferences and buy list are their own, never the Mac's file."""
import threading

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.services import box_scout


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
