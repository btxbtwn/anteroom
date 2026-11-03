"""Chrome inventory import on the web: the seller's own listings, read through their Anteroom tab."""
from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_hosted_vinted import _answer, _in_background
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.services import hosted_browser_relay

CAPABLE = 'marketplace.actions,marketplace.import'
POST = 'b' * 24


def test_chrome_import_reads_the_sellers_own_listings_through_their_tab(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, _ = cloud
    monkeypatch.setattr('vendoo_studio.routes.hosted_browser_relay.WAIT_S', 0.5)
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    hosted_browser_relay._relays.clear()

    assert client.get('/api/chrome-import/poshmark/active').status_code == 401
    assert client.get('/api/chrome-import/facebook/active', headers=alice).status_code == 404
    refused = client.get('/api/chrome-import/poshmark/active', headers=alice)
    assert refused.status_code == 503 and 'connect Chrome first' in refused.json()['detail']
    # A Bridge from before the import reads asks for an update instead of being sent one.
    client.get('/api/browser-relay/next?capabilities=marketplace.actions', headers=alice)
    old = client.get('/api/chrome-import/poshmark/active', headers=alice)
    assert old.status_code == 503 and '0.2.53' in old.json()['detail']

    client.get('/api/browser-relay/next?capabilities=' + CAPABLE, headers=alice)
    # Bob's account never reaches Alice's Chrome.
    assert client.get('/api/chrome-import/poshmark/active', headers=bob).status_code == 503
    thread, result = _in_background(lambda: client.get('/api/chrome-import/poshmark/active', headers=alice))
    command = _answer(client, alice, {'ok': True, 'next_cursor': 'next-1', 'rows': [
        {'id': POST, 'title': 'Denim jacket', 'price': 45, 'quantity': 2}, {'id': 'not-a-post', 'title': 'x'}]}, CAPABLE)
    thread.join(10)
    assert command['type'] == 'marketplace.import.page' and command['job_id'] is None
    assert command['payload'] == {'marketplace': 'poshmark', 'request_id': command['message_id']}
    page = result['response'].json()
    assert page['next_cursor'] == 'next-1' and [row['id'] for row in page['rows']] == [POST]
    assert page['rows'][0]['conversation_id'] is None

    thread, result = _in_background(lambda: client.get('/api/chrome-import/poshmark/active?cursor=next-1', headers=alice))
    command = _answer(client, alice, {'ok': True, 'next_cursor': None, 'rows': []}, CAPABLE)
    thread.join(10)
    assert command['payload']['cursor'] == 'next-1'

    # The Bridge's refusal reaches the seller in its own words, as on the Mac.
    thread, result = _in_background(lambda: client.post('/api/chrome-import/poshmark', headers=alice, json={'listing': POST}))
    _answer(client, alice, {'ok': False, 'error': 'This listing does not belong to the signed-in Poshmark account.',
                            'error_code': 'rejected'}, CAPABLE)
    thread.join(10)
    assert result['response'].status_code == 409
    assert result['response'].json()['detail'] == 'This listing does not belong to the signed-in Poshmark account.'

    thread, result = _in_background(lambda: client.post('/api/chrome-import/poshmark', headers=alice,
                                                        json={'listing': f'https://poshmark.com/listing/Denim-{POST}'}))
    command = _answer(client, alice, {'ok': True, 'listing': {
        'id': POST, 'title': 'Denim jacket', 'description': 'Classic fit.', 'price': 45, 'currency': 'USD', 'quantity': 1,
        'brand': "Levi's", 'condition': 'not_nwt', 'photos': []}}, CAPABLE)
    thread.join(10)
    assert command['type'] == 'marketplace.import.listing'
    assert command['payload'] == {'marketplace': 'poshmark', 'listing_id': POST, 'request_id': command['message_id']}
    imported = result['response'].json()
    assert result['response'].status_code == 200, imported
    assert imported['reused'] is False and imported['listing_id'] == POST

    # The item is Alice's alone, and importing again reopens it without asking Chrome.
    assert client.get(f"/api/conversations/{imported['conversation_id']}", headers=alice).status_code == 200
    assert client.get(f"/api/conversations/{imported['conversation_id']}", headers=bob).status_code == 404
    again = client.post('/api/chrome-import/poshmark', headers=alice, json={'listing': POST}).json()
    assert again['reused'] is True and again['conversation_id'] == imported['conversation_id']


def test_heartbeat_accepts_the_import_bridge_capabilities(cloud, signing_keys):  # noqa: F811
    client, _ = cloud
    capabilities = ['marketplace.actions', 'vinted.choices', 'sellwild.account', 'marketplace.sessions',
                    'marketplace.sales_history', 'marketplace.import']
    response = client.post('/api/browser-relay/heartbeat', headers=headers(signing_keys), json={'capabilities': capabilities})
    assert response.status_code == 200, response.text
