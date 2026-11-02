"""Facebook on the web, as on the Mac: link an item already on Facebook, check it in the seller's Chrome, delete it there."""
from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_hosted_vinted import _answer, _in_background
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.services import hosted_browser_relay

CAPABLE = 'marketplace.actions,facebook.listing'
LINK = 'https://www.facebook.com/marketplace/item/1234567890/'


def test_a_linked_facebook_listing_is_checked_in_the_sellers_chrome(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, _ = cloud
    monkeypatch.setattr('vendoo_studio.routes.hosted_browser_relay.WAIT_S', 0.5)
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    hosted_browser_relay._relays.clear()
    runtime = client.get('/api/runtime', headers=alice).json()
    assert 'facebook' in runtime['marketplace_forms']
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Lamp'}).json()['id']
    base = f'/api/conversations/{identity}/marketplace-listings'

    assert client.post(base + '/facebook/check', headers=alice).json()['detail'] == 'Anteroom has no Facebook listing link for this item.'
    marked = client.post(base + '/facebook/mark', headers=alice, json={'state': 'listed', 'url': LINK})
    assert marked.status_code == 200, marked.text
    assert client.post(base + '/facebook/check', headers=bob).status_code == 404
    refused = client.post(base + '/facebook/check', headers=alice)
    assert refused.status_code == 409 and refused.json()['detail'] == 'Open Anteroom in Chrome on your computer and connect Chrome first.'

    client.get('/api/browser-relay/next?capabilities=' + CAPABLE, headers=alice)
    thread, result = _in_background(lambda: client.post(base + '/facebook/check', headers=alice))
    command = _answer(client, alice, {'ok': True, 'state': 'live', 'owner': 'you', 'html': '<secret>'}, CAPABLE)
    thread.join(10)
    assert command['type'] == 'marketplace.listing_state'
    assert command['payload'] == {'marketplace': 'facebook', 'listing_id': '1234567890', 'request_id': command['message_id']}
    checked = result['response'].json()
    assert checked['ok'] is True and checked['state'] == 'live' and checked['owner'] == 'you' and checked['sale_recorded'] is False
    assert '<secret>' not in result['response'].text

    thread, result = _in_background(lambda: client.post(base + '/facebook/check', headers=alice))
    _answer(client, alice, {'ok': False, 'error_code': 'signed_out', 'error': 'Sign in to Facebook in Chrome, then try again.'}, CAPABLE)
    thread.join(10)
    assert result['response'].json()['error'] == 'Sign in to Facebook in Chrome, then try again.'

    # Facebook listings are deleted on Facebook, as on the Mac.
    delist = client.post(base + '/delist', headers=alice, json={'marketplaces': ['facebook'], 'confirm': True})
    assert delist.status_code == 409 and 'Delete it on Facebook' in delist.json()['detail']
