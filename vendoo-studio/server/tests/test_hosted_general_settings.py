from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import jwks, signing_keys  # noqa: F401
from vendoo_studio.version import app_version


def test_appearance_preferences_survive_requests_and_stay_in_the_account(cloud, signing_keys):  # noqa: F811
    client, _engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    endpoint = '/api/settings/ui'
    original = client.get(endpoint, headers=bob).json()
    changed = client.put(endpoint, headers=alice, json={'theme': 'light', 'listing_names': 'sku'})
    assert changed.status_code == 200, changed.text
    saved = client.get(endpoint, headers=alice).json()
    assert (saved['theme'], saved['listing_names']) == ('light', 'sku')
    assert client.put(endpoint, headers=alice, json={'theme': 'system', 'listing_names': 'title'}).status_code == 200
    saved = client.get(endpoint, headers=alice).json()
    assert (saved['theme'], saved['listing_names']) == ('system', 'title')
    assert client.get(endpoint, headers=bob).json() == original
    for invalid in ({'theme': 'invalid'}, {'listing_names': 'password'}):
        assert client.put(endpoint, headers=alice, json=invalid).status_code == 422


def test_shared_release_notes_require_sign_in_and_do_not_mount_desktop_update_actions(cloud, signing_keys):  # noqa: F811
    client, _engine = cloud
    assert client.get('/api/changelog').status_code == 401
    response = client.get('/api/changelog', headers=headers(signing_keys))
    assert response.status_code == 200, response.text
    notes = response.json()
    assert notes['version'] == app_version()
    assert notes['entries'][0]['version'] == app_version()
    assert notes['entries'][0]['body']
    assert response.headers['cache-control'] == 'no-store'
    for path in ('/api/updates', '/api/updates/progress'):
        assert client.get(path, headers=headers(signing_keys)).status_code == 404
    for action in ('download', 'restart', 'apply'):
        assert client.post('/api/updates/' + action, headers=headers(signing_keys)).status_code == 404
