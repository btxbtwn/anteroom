"""The hosted UI gate opens only with imports, sale settings and promotions mounted."""
from test_hosted_drafts import cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.services import hosted_browser_relay


def test_runtime_exposes_complete_chrome_session_workflows(cloud, signing_keys):  # noqa: F811
    client, _ = cloud
    alice = headers(signing_keys)
    hosted_browser_relay._relays.clear()
    from types import SimpleNamespace
    assert 'marketplace_sessions' not in client.get('/api/runtime', headers=alice).json()['features']
    client.app.state.sale_checks = SimpleNamespace(done=lambda: False)
    client.app.state.promotion_scheduler = SimpleNamespace(done=lambda: False)
    runtime = client.get('/api/runtime', headers=alice).json()
    assert 'marketplace_sessions' in runtime['features']
    for marketplace in ('poshmark', 'mercari', 'depop', 'sellwild', 'vinted'):
        response = client.get(f'/api/chrome-import/{marketplace}/active', headers=alice)
        assert response.status_code == 503 and 'connect Chrome first' in response.json()['detail']
    for path in ('/api/promotion-automations', '/api/promotions', '/api/settings/sale-detection'):
        assert client.get(path, headers=alice).status_code == 200
        assert client.get(path).status_code == 401
    for marketplace in ('facebook', 'grailed', 'vendoo'):
        assert marketplace not in runtime['marketplace_forms']
        assert client.get(f'/api/chrome-import/{marketplace}/active', headers=alice).status_code == 404

    client.app.state.promotion_scheduler = SimpleNamespace(done=lambda: True)
    assert 'marketplace_sessions' not in client.get('/api/runtime', headers=alice).json()['features']


def test_heartbeat_accepts_the_complete_bridge_capabilities(cloud, signing_keys):  # noqa: F811
    client, _ = cloud
    capabilities = ['marketplace.actions', 'vinted.choices', 'sellwild.account', 'marketplace.sessions',
                    'marketplace.sales_history', 'marketplace.promotions', 'marketplace.import']
    response = client.post('/api/browser-relay/heartbeat', headers=headers(signing_keys), json={'capabilities': capabilities})
    assert response.status_code == 200, response.text
    unknown = client.post('/api/browser-relay/heartbeat', headers=headers(signing_keys),
                          json={'capabilities': [*capabilities, 'arbitrary.proxy']})
    assert unknown.status_code == 422
