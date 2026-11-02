"""Sellwild on the web: its native form with the account's own choices from the seller's Chrome, and its actions."""

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_hosted_vinted import _answer, _in_background
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.services import hosted_browser_relay

CAPABLE = 'marketplace.actions,sellwild.account'


def _draft(client, alice):
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Tee'}).json()['id']
    saved = client.put(f'/api/conversations/{identity}/listing', headers=alice, json={
        'listing': {'title': 'Nike L Tee', 'description': 'Details', 'price': 20, 'brand': 'Nike'}, 'expected_revision_id': ''})
    assert saved.status_code == 200, saved.text
    return identity, saved.json()['revision_id']


def test_sellwild_form_reads_the_account_choices_in_the_sellers_chrome(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, _ = cloud
    monkeypatch.setattr('vendoo_studio.routes.hosted_browser_relay.WAIT_S', 0.5)
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    hosted_browser_relay._relays.clear()
    identity, revision = _draft(client, alice)
    url = f'/api/conversations/{identity}/native-marketplaces/sellwild/form?revision_id={revision}'
    assert 'sellwild' in client.get('/api/runtime', headers=alice).json()['marketplace_forms']

    # No Anteroom tab connects Chrome: the product fields load and the account choices say why they are missing.
    form = client.get(url, headers=alice)
    assert form.status_code == 200, form.text
    body = form.json()
    assert body['account_connected'] is False
    assert body['notes'] == ['Open Anteroom in Chrome on your computer and connect Chrome first.']
    fields = {row['label']: row for row in body['fields']}
    assert fields['Shipping From']['help'] == 'Sign in to Sellwild in Chrome, then reload choices to show your address.'
    assert next(row for row in body['fields'] if row['path'] == 'sellwild_specifics.title')['value'] == 'Nike L Tee'
    assert client.get(url, headers=bob).status_code == 404

    client.get('/api/browser-relay/next?capabilities=' + CAPABLE, headers=alice)
    thread, result = _in_background(lambda: client.get(url, headers=alice))
    command = _answer(client, alice, {'ok': True, 'ship_from': 'Pat Seller\n1 Main St', 'default_parcel': 'USPS_SoftPack',
                                      'coupons': [{'id': 117, 'name': '25OFF', 'discountType': 'percentage', 'discountValue': 25},
                                                  {'name': 'no id'}]}, CAPABLE)
    thread.join(10)
    assert command['type'] == 'marketplace.sellwild_account' and command['payload'] == {'request_id': command['message_id']}
    body = result['response'].json()
    assert body['account_connected'] is True and 'notes' not in body
    fields = {row['label']: row for row in body['fields']}
    assert fields['Shipping From']['value'] == 'Pat Seller\n1 Main St'
    assert fields['Sell it faster with a discount']['options'] == ['25% 25OFF']

    # Signed out of Sellwild in that Chrome.
    thread, result = _in_background(lambda: client.get(url, headers=alice))
    _answer(client, alice, {'ok': False, 'error_code': 'signed_out'}, CAPABLE)
    thread.join(10)
    assert result['response'].json()['notes'] == ["Sign in to Sellwild in Chrome, then reload Sellwild's choices."]


def test_sellwild_choices_and_actions_are_offered_on_the_web(cloud, signing_keys):  # noqa: F811
    client, _ = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    hosted_browser_relay._relays.clear()
    identity, revision = _draft(client, alice)
    base = f'/api/conversations/{identity}'
    options = client.get(base + '/native-marketplaces/sellwild/options', headers=alice)
    assert options.status_code == 200 and options.json()['categories'] and options.json()['conditions']
    assert client.get(base + '/native-marketplaces/sellwild/options', headers=bob).status_code == 404
    assert client.post(base + '/native-marketplaces/sellwild/list', headers=alice, json={'revision_id': revision}).status_code == 400
    refused = client.post(base + '/native-marketplaces/sellwild/list', headers=alice, json={'revision_id': revision, 'confirm': True})
    assert refused.status_code == 409 and 'connect Chrome first' in refused.json()['detail']
    refused = client.post(base + '/marketplace-listings/delist', headers=alice, json={'marketplaces': ['sellwild'], 'confirm': True})
    assert refused.status_code == 409 and 'Mac app' not in refused.json()['detail']


