"""Managed costs and provider limits on the web are private to each seller."""
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from anteroom.models.api_usage import ApiUsageEvent
from anteroom.services import claude_auth, usage_limits
from anteroom.services.service_context import current


def test_managed_usage_is_private_and_counts_only_the_last_30_days(cloud, signing_keys):  # noqa: F811
    client, engine = cloud
    path = '/api/settings/managed-usage'
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    now = datetime.now(UTC)
    with Session(engine) as db:
        for provider, cost, age in [
            ('openrouter', 0.004, 0), ('openrouter', 0.01, 2),
            ('photoroom', 0.1, 29), ('photoroom', 2, 31),
        ]:
            db.add(ApiUsageEvent(user_id=USER_ID, provider=provider, cost_usd=cost,
                                 cost_source='rate_card', kind='test', created_at=now - timedelta(days=age)))
        db.commit()

    assert client.get(path).status_code == 401
    result = client.get(path, headers=alice)
    assert result.status_code == 200
    assert result.json() == {'openrouter': {'count': 2, 'usd': 0.014},
                             'photoroom': {'count': 1, 'usd': 0.1}}
    assert client.get(path, headers=bob, params={'seller_id': USER_ID}).json() == {
        'openrouter': {'count': 0, 'usd': 0}, 'photoroom': {'count': 0, 'usd': 0},
    }
    with Session(engine) as db:
        db.add(ApiUsageEvent(user_id=BOB, provider='photoroom', cost_usd=0.20,
                             cost_source='rate_card', kind='edit'))
        db.commit()
    assert client.get(path, headers=bob).json()['photoroom'] == {'count': 1, 'usd': 0.20}
    assert client.get(path, headers=alice).json() == result.json()


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
    assert 'usage' not in client.get('/api/runtime', headers=alice).json()['views']
    usage_limits.clear_cache()
