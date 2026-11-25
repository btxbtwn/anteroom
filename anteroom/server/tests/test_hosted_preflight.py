"""The marketplace preflight reads the database without holding up the web server.

Its work is all synchronous database and form building. Run on the event loop,
a slow one stopped every other request; with a few of them at once (opening
marketplace tabs one after another) they also held every pooled connection
while waiting for one more, until the pool timed out.
"""
import threading

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
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

    from anteroom.services import job_snapshot

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


def test_opening_a_listing_shares_one_preflight_build(cloud, signing_keys, monkeypatch):  # noqa: F811
    """Opening one listing asks from every pane at once (production 2026-11-25: five or six
    preflights held pooled connections together). They share one build in one session, and
    another seller asking about the same listing never shares it."""
    from sqlalchemy import event

    from anteroom.services import job_snapshot, native_marketplaces

    client, engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    draft = client.post('/api/drafts', headers=alice, json={'title': 'Vintage coat'}).json()
    saved = client.patch(f"/api/drafts/{draft['id']}", headers=alice, json={
        'title': 'Vintage coat', 'notes': '', 'expected_updated_at': draft['updated_at'],
        'fields': {'price': 40, 'brand': 'Pendleton', 'condition': 'Good', 'description': 'Warm wool coat.'},
    })
    assert saved.status_code == 200, saved.text

    panes = 5
    arrived, joined, builds = [], threading.Event(), []
    share, prepare = native_marketplaces.shared_preflight, job_snapshot.prepare_listing_snapshot

    async def counting(*args):
        arrived.append(1)
        if len(arrived) == panes:
            joined.set()
        return await share(*args)

    def slow_prepare(*args, **kwargs):
        builds.append(1)
        joined.wait(10)
        return prepare(*args, **kwargs)

    monkeypatch.setattr(native_marketplaces, 'shared_preflight', counting)
    monkeypatch.setattr(job_snapshot, 'prepare_listing_snapshot', slow_prepare)
    checkouts = []
    record = lambda *_: checkouts.append(threading.current_thread().name)  # noqa: E731
    url = f"/api/conversations/{draft['id']}/native-marketplaces/preflight"
    replies = []
    event.listen(engine, 'checkout', record)
    try:
        readers = [threading.Thread(target=lambda: replies.append(client.get(url, headers=alice)))
                   for _ in range(panes)]
        for reader in readers:
            reader.start()
        for reader in readers:
            reader.join(20)
    finally:
        event.remove(engine, 'checkout', record)
    assert [reply.status_code for reply in replies] == [200] * panes, [reply.text for reply in replies]
    assert all(reply.json() == replies[0].json() for reply in replies)
    assert len(builds) == 1
    assert len(checkouts) <= 2, checkouts
    assert native_marketplaces._preflight_builds == {}
    assert client.get(url, headers=bob).status_code == 404
