"""Vinted on the web: its form's categories and brands come from the seller's own Chrome, and its actions go there too."""
import threading

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.services import hosted_browser_relay

READS = 'marketplace.actions,vinted.choices'


def _answer(client, alice, reply):
    """Act as the seller's Anteroom tab: take the next command for their Chrome and post the Bridge's reply."""
    for _ in range(20):
        command = client.get('/api/browser-relay/next?capabilities=' + READS, headers=alice)
        if command.status_code == 200:
            body = command.json()
            assert client.post('/api/browser-relay/replies', headers=alice,
                               json={'request_id': body['message_id'], 'payload': reply}).status_code == 200
            return body
    raise AssertionError('No command reached the tab')


def _in_background(call):
    result = {}
    thread = threading.Thread(target=lambda: result.update(response=call()), daemon=True)
    thread.start()
    return thread, result


def test_vinted_choices_are_read_in_the_sellers_own_chrome(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, _ = cloud
    monkeypatch.setattr('vendoo_studio.routes.hosted_browser_relay.WAIT_S', 0.5)
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    hosted_browser_relay._relays.clear()
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Jeans'}).json()['id']
    base = f'/api/conversations/{identity}/native-marketplaces/vinted'

    runtime = client.get('/api/runtime', headers=alice).json()
    assert 'vinted' in runtime['marketplace_forms']
    # No tab is open: the seller is told to connect Chrome rather than waiting.
    refused = client.get(base + '/categories', headers=alice)
    assert refused.status_code == 409 and 'connect Chrome' in refused.json()['detail']
    # A Bridge that cannot run reads yet asks for an update.
    client.get('/api/browser-relay/next?capabilities=marketplace.actions', headers=alice)
    old = client.get(base + '/categories', headers=alice)
    assert old.status_code == 409 and '0.2.42' in old.json()['detail']
    assert client.get(base + '/categories', headers=bob).status_code == 404

    client.get('/api/browser-relay/next?capabilities=' + READS, headers=alice)
    thread, result = _in_background(lambda: client.get(base + '/categories', headers=alice))
    command = _answer(client, alice, {'ok': True, 'options': ['Women > Clothing > Jeans > Straight jeans', 7]})
    thread.join(10)
    assert command['type'] == 'marketplace.vinted_categories' and set(command) == {'type', 'job_id', 'message_id', 'payload'}
    assert command['payload'] == {'request_id': command['message_id']}
    assert result['response'].status_code == 200
    assert result['response'].json() == {'options': ['Women > Clothing > Jeans > Straight jeans']}

    thread, result = _in_background(lambda: client.get(base + '/brands?q=Lev&category=Women+%3E+Jeans', headers=alice))
    command = _answer(client, alice, {'ok': True, 'options': ["Levi's"]})
    thread.join(10)
    assert command['type'] == 'marketplace.vinted_brands'
    assert command['payload'] == {'query': 'Lev', 'category': 'Women > Jeans', 'request_id': command['message_id']}
    assert result['response'].json() == {'options': ["Levi's"]}

    # Signed out of Vinted in that Chrome: the seller is told where to sign in.
    thread, result = _in_background(lambda: client.get(base + '/categories', headers=alice))
    _answer(client, alice, {'ok': False, 'error_code': 'signed_out'})
    thread.join(10)
    assert result['response'].status_code == 409
    assert result['response'].json()['detail'] == "Sign in to Vinted in Chrome, then reload Vinted's choices."


def test_vinted_actions_are_offered_on_the_web(cloud, signing_keys):  # noqa: F811
    client, _ = cloud
    alice = headers(signing_keys)
    hosted_browser_relay._relays.clear()
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Jeans'}).json()['id']
    base = f'/api/conversations/{identity}'
    # List still needs the seller's confirmation in the same call.
    assert client.post(base + '/native-marketplaces/vinted/list', headers=alice,
                       json={'revision_id': 'r1'}).status_code == 400
    from vendoo_studio.database import SessionLocal
    from vendoo_studio.repositories.queries import ListingRepo
    from vendoo_studio.services.hosted_context import for_seller
    from vendoo_studio.services.service_context import use
    with use(for_seller(USER_ID)), SessionLocal() as db:
        revision = ListingRepo(db).save_revision(identity, {'title': 'Straight jeans', 'price': 20}, 'test').id
    # A confirmed List with no Anteroom tab connecting Chrome is refused before anything starts.
    refused = client.post(base + '/native-marketplaces/vinted/list', headers=alice, json={'revision_id': revision, 'confirm': True})
    assert refused.status_code == 409 and 'connect Chrome first' in refused.json()['detail']
    # Delist reaches the Mac's own handler, which finds nothing on Vinted to remove.
    refused = client.post(base + '/marketplace-listings/delist', headers=alice, json={'marketplaces': ['vinted'], 'confirm': True})
    assert refused.status_code == 409 and refused.json()['detail'] == 'Anteroom has no vinted listing link for this item.'
