"""The Usage page on the web reads each seller's own providers and never starts a CLI."""
from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import jwks, signing_keys  # noqa: F401
from vendoo_studio.services import claude_auth, usage_limits
from vendoo_studio.services.service_context import current


def test_usage_is_per_seller_and_never_probes_the_servers_claude_login(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, _ = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    usage_limits.clear_cache()
    monkeypatch.setattr(claude_auth, 'claude_signed_in', lambda: (_ for _ in ()).throw(AssertionError('probed the server')))

    async def cursor():
        return usage_limits.make_limits('cursor', usage_limits.now_iso(), [{
            'id': current().seller_id, 'kind': 'monthly', 'label': 'Overall', 'used_percent': 10}])

    monkeypatch.setitem(usage_limits.FETCHERS, 'cursor', cursor)
    assert client.get('/api/settings/usage-limits').status_code == 401
    rows = {}
    for name, auth in (('alice', alice), ('bob', bob)):
        response = client.get('/api/settings/usage-limits', headers=auth)
        assert response.status_code == 200, response.text
        rows[name] = {row['provider']: row for row in response.json()['providers']}
    assert list(rows['alice']) == list(usage_limits.PROVIDER_ORDER)
    assert rows['alice']['claude']['unavailable']['message'] == 'Claude sign-in usage is shown in the Mac app.'
    # A cached answer for one seller is never served to another.
    assert rows['alice']['cursor']['windows'][0]['id'] != rows['bob']['cursor']['windows'][0]['id']
    assert 'usage' in client.get('/api/runtime', headers=alice).json()['views']
    usage_limits.clear_cache()
