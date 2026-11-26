"""Managed costs and provider limits on the web are private to each seller."""
import csv
import io
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from anteroom.models.api_usage import ApiUsageEvent
from anteroom.services import claude_auth, usage_limits
from anteroom.services.service_context import current


def _service(body, name):
    return {key: body[name][key] for key in ('count', 'usd')}


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
    assert client.get(path + '.csv').status_code == 401
    result = client.get(path, headers=alice)
    assert result.status_code == 200
    body = result.json()
    assert _service(body, 'openrouter') == {'count': 2, 'usd': 0.014}
    assert _service(body, 'photoroom') == {'count': 1, 'usd': 0.1}
    assert body['total_usd'] == 0.114
    # Thirty UTC days ending today, each present so the chart has no gaps.
    assert body['period']['id'] == '30d'
    assert [day['date'] for day in body['days']] == [
        (now - timedelta(days=age)).date().isoformat() for age in range(29, -1, -1)]
    assert body['days'][0] == {'date': body['days'][0]['date'], 'openrouter': 0, 'photoroom': 0.1}
    assert body['days'][-1]['openrouter'] == 0.004 and body['days'][-3]['openrouter'] == 0.01
    assert round(sum(day['openrouter'] + day['photoroom'] for day in body['days']), 6) == body['total_usd']

    other = client.get(path, headers=bob, params={'seller_id': USER_ID}).json()
    assert other['total_usd'] == 0 and other['openrouter']['items'] == [] and other['photoroom']['items'] == []
    assert all(day['openrouter'] == 0 and day['photoroom'] == 0 for day in other['days'])
    assert client.get(path + '.csv', headers=bob).text.splitlines() == [
        'time_utc,service,item,input_tokens,output_tokens,cost_usd']
    with Session(engine) as db:
        db.add(ApiUsageEvent(user_id=BOB, provider='photoroom', cost_usd=0.20,
                             cost_source='rate_card', kind='edit'))
        db.commit()
    assert _service(client.get(path, headers=bob).json(), 'photoroom') == {'count': 1, 'usd': 0.20}
    assert client.get(path, headers=alice).json() == body


def test_managed_usage_by_month_breaks_each_service_down_and_exports_every_charge(cloud, signing_keys):  # noqa: F811
    client, engine = cloud
    path = '/api/settings/managed-usage'
    alice = headers(signing_keys)
    events = [
        # (provider, item, cost, tokens in, tokens out, when)
        ('openrouter', 'google/gemini-2.5-flash', 0.02, 1200, 300, datetime(2026, 9, 30, 23, 59, 59)),
        ('openrouter', 'google/gemini-2.5-flash', 0.03, 2000, 500, datetime(2026, 10, 1, 0, 0, 0)),
        ('openrouter', 'google/gemini-2.5-flash', 0.0125, 800, 200, datetime(2026, 10, 14, 15, 30, 0)),
        ('openrouter', 'openai/gpt-5-mini', 0.0004, 90, 10, datetime(2026, 10, 14, 16, 0, 0)),
        ('photoroom', 'Background removed', 0.1, None, None, datetime(2026, 10, 14, 18, 0, 0)),
        ('photoroom', 'Background removed', 0.1, None, None, datetime(2026, 10, 31, 23, 59, 59)),
        ('photoroom', '=Ghost mannequin', 0.1, None, None, datetime(2026, 10, 20, 9, 0, 0)),
        ('photoroom', 'Flat lay', 0.1, None, None, datetime(2026, 11, 1, 0, 0, 0)),
    ]
    with Session(engine) as db:
        for provider, item, cost, tokens_in, tokens_out, when in events:
            db.add(ApiUsageEvent(user_id=USER_ID, provider=provider, model_or_edit=item, cost_usd=cost,
                                 cost_source='rate_card', kind='test', input_tokens=tokens_in,
                                 output_tokens=tokens_out, created_at=when))
        db.commit()

    body = client.get(path, headers=alice, params={'period': '2026-10'}).json()
    assert body['period'] == {'id': '2026-10', 'start': '2026-10-01T00:00:00+00:00', 'end': '2026-11-01T00:00:00+00:00'}
    assert body['total_usd'] == 0.3429
    assert body['openrouter'] == {
        'count': 3, 'usd': 0.0429, 'input_tokens': 2890, 'output_tokens': 710,
        'items': [
            {'label': 'google/gemini-2.5-flash', 'count': 2, 'usd': 0.0425, 'input_tokens': 2800, 'output_tokens': 700},
            {'label': 'openai/gpt-5-mini', 'count': 1, 'usd': 0.0004, 'input_tokens': 90, 'output_tokens': 10},
        ],
    }
    assert [(item['label'], item['count'], item['usd']) for item in body['photoroom']['items']] == [
        ('Background removed', 2, 0.2), ('=Ghost mannequin', 1, 0.1)]
    assert len(body['days']) == 31
    spent = {day['date']: day for day in body['days'] if day['openrouter'] or day['photoroom']}
    assert spent == {
        '2026-10-01': {'date': '2026-10-01', 'openrouter': 0.03, 'photoroom': 0},
        '2026-10-14': {'date': '2026-10-14', 'openrouter': 0.0129, 'photoroom': 0.1},
        '2026-10-20': {'date': '2026-10-20', 'openrouter': 0, 'photoroom': 0.1},
        '2026-10-31': {'date': '2026-10-31', 'openrouter': 0, 'photoroom': 0.1},
    }
    # Every month from the first charge to this one, newest first, a year at most.
    now = datetime.now(UTC)
    assert body['months'][0] == f'{now:%Y-%m}'
    assert body['months'][-1] == '2026-09' or len(body['months']) == 12
    assert len(set(body['months'])) == len(body['months'])

    export = client.get(path + '.csv', headers=alice, params={'period': '2026-10'})
    assert export.headers['content-type'].startswith('text/csv')
    assert 'anteroom-usage-2026-10.csv' in export.headers['content-disposition']
    rows = list(csv.reader(io.StringIO(export.text)))
    assert rows[0] == ['time_utc', 'service', 'item', 'input_tokens', 'output_tokens', 'cost_usd']
    assert rows[1] == ['2026-10-01 00:00:00', 'openrouter', 'google/gemini-2.5-flash', '2000', '500', '0.03']
    assert len(rows) == 7 and round(sum(float(row[5]) for row in rows[1:]), 6) == body['total_usd']
    # A spreadsheet must read an item as text, never as a formula.
    assert ["'=Ghost mannequin"] == [row[2] for row in rows if 'Ghost' in row[2]]

    for bad in ('2026-13', '2026-1', '90d', 'all', '1999-01'):
        assert client.get(path, headers=alice, params={'period': bad}).status_code == 422
        assert client.get(path + '.csv', headers=alice, params={'period': bad}).status_code == 422


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
