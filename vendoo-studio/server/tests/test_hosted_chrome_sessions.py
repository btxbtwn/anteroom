"""Settings → Connections on the web: Chrome's marketplace sign-ins, read through the seller's own Anteroom tab."""
from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_hosted_vinted import _answer, _in_background
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.services import hosted_browser_relay

CAPABLE = 'marketplace.actions,marketplace.sessions'


def test_connections_read_chrome_sign_ins_through_the_sellers_tab(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, _ = cloud
    monkeypatch.setattr('vendoo_studio.routes.hosted_browser_relay.WAIT_S', 0.5)
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    hosted_browser_relay._relays.clear()

    assert client.get('/api/marketplaces/sessions').status_code == 401
    refused = client.get('/api/marketplaces/sessions', headers=alice)
    assert refused.status_code == 409 and refused.json()['detail'] == 'Open Anteroom in Chrome on your computer and connect Chrome first.'
    client.get('/api/browser-relay/next?capabilities=marketplace.actions', headers=alice)
    old = client.get('/api/marketplaces/sessions', headers=alice)
    assert old.status_code == 409 and '0.2.44' in old.json()['detail']

    client.get('/api/browser-relay/next?capabilities=' + CAPABLE, headers=alice)
    # Bob's account never reaches Alice's Chrome.
    assert client.get('/api/marketplaces/sessions', headers=bob).status_code == 409
    thread, result = _in_background(lambda: client.get('/api/marketplaces/sessions', headers=alice))
    command = _answer(client, alice, {'ok': True, 'sessions': {
        'poshmark': {'state': 'signed_in', 'expires_at': 1900000000, 'label': 'pat', 'cookie': 'secret-cookie'},
        'vinted': {'state': 'signed_out'}, 'ebay': {'state': 'signed_in'}}}, CAPABLE)
    thread.join(10)
    assert command['type'] == 'marketplace.sessions' and command['payload'] == {'request_id': command['message_id']}
    sessions = result['response'].json()['sessions']
    assert set(sessions) == {'depop', 'poshmark', 'mercari', 'sellwild', 'vinted', 'facebook'}
    assert sessions['poshmark'] == {'state': 'signed_in', 'expires_at': 1900000000, 'label': 'pat', 'expiring': False}
    assert sessions['vinted']['state'] == 'signed_out' and sessions['depop']['state'] == 'unknown'
    assert 'secret-cookie' not in result['response'].text

    # Sign in opens that marketplace's sign-in page in the seller's Chrome, when they ask.
    assert client.post('/api/marketplaces/ebay/connect', headers=alice).status_code == 404
    thread, result = _in_background(lambda: client.post('/api/marketplaces/mercari/connect', headers=alice))
    command = _answer(client, alice, {'ok': True, 'signed_in': True, 'state': 'signed_in', 'label': 'pat', 'token': 'secret'}, CAPABLE)
    thread.join(10)
    assert command['type'] == 'marketplace.connect' and command['payload']['marketplace'] == 'mercari'
    assert result['response'].json() == {'ok': True, 'signed_in': True, 'state': 'signed_in', 'expires_at': None,
                                         'label': 'pat', 'expiring': False}
