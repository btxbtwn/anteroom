"""The marketplace preflight reads the database without holding up the web server.

Its work is all synchronous database and form building. Run on the event loop,
a slow one stopped every other request; with a few of them at once (opening
marketplace tabs one after another) they also held every pooled connection
while waiting for one more, until the pool timed out.
"""
import threading

from test_hosted_drafts import cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import jwks, signing_keys  # noqa: F401


def test_preflight_runs_off_the_event_loop(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, _ = cloud
    alice = headers(signing_keys)
    draft = client.post('/api/drafts', headers=alice, json={'title': 'Vintage coat'}).json()
    saved = client.patch(f"/api/drafts/{draft['id']}", headers=alice, json={
        'title': 'Vintage coat', 'notes': '', 'expected_updated_at': draft['updated_at'],
        'fields': {'price': 40, 'brand': 'Pendleton', 'condition': 'Good', 'description': 'Warm wool coat.'},
    })
    assert saved.status_code == 200, saved.text

    from vendoo_studio.services import job_snapshot

    started, release = threading.Event(), threading.Event()
    prepare = job_snapshot.prepare_listing_snapshot

    def slow_prepare(*args, **kwargs):
        started.set()
        release.wait(15)
        return prepare(*args, **kwargs)

    monkeypatch.setattr(job_snapshot, 'prepare_listing_snapshot', slow_prepare)
    replies = {}
    reading = threading.Thread(target=lambda: replies.update(
        preflight=client.get(f"/api/conversations/{draft['id']}/native-marketplaces/preflight", headers=alice)))
    reading.start()
    try:
        assert started.wait(10)
        # While that preflight is still building, the server answers everyone else.
        other = threading.Thread(target=lambda: replies.update(health=client.get('/api/health')))
        other.start()
        other.join(5)
        assert not other.is_alive(), 'a running preflight held up another request'
    finally:
        release.set()
        reading.join(15)
    assert replies['health'].status_code == 200
    assert replies['preflight'].status_code == 200
    assert replies['preflight'].json()['revision_id']
