import asyncio

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.models.job import Job
from vendoo_studio.models.listing import Listing, ListingRevision
from vendoo_studio.services.hosted_context import for_seller
from vendoo_studio.services.hosted_database import SellerDatabaseSession
from vendoo_studio.services.native_editor import editor_form
from vendoo_studio.services.service_context import use

MARKETS = ('poshmark', 'depop', 'mercari')


def test_hosted_forms_match_desktop_catalogs_and_keep_owned_picks_without_jobs(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    from vendoo_studio.services import mercari_api

    async def no_quotes(*_args):
        raise OSError('offline')

    # Mercari's anonymous shipping quote is the form's only outbound read.
    monkeypatch.setattr(mercari_api, 'shipping_quotes', no_quotes)
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Private coat'}).json()['id']
    path = '/api/conversations/' + identity
    listing = {'title': 'Private wool coat', 'description': 'Only Alice can see this copy',
               'price': 42, 'quantity': 1, 'brand': 'Pendleton', 'size': 'M'}
    saved = client.put(path + '/listing', headers=alice,
                       json={'listing': listing, 'expected_revision_id': ''})
    assert saved.status_code == 200, saved.text
    saved = saved.json()
    revision = saved['revision_id']
    for market in MARKETS:
        url = path + '/native-marketplaces/' + market + '/form'
        assert client.get(url).status_code == 401
        assert client.get(url, headers=bob).status_code == 404
        response = client.get(url + '?revision_id=' + revision, headers=alice)
        assert response.status_code == 200, response.text
        with use(for_seller(USER_ID)), SellerDatabaseSession(user_id=USER_ID, bind=engine) as db:
            expected = asyncio.run(editor_form(identity, market, revision, db))
        assert response.json() == expected
        category = next(row for row in expected['fields'] if row['path'] == 'direct_category_ids.' + market)
        choice = next(iter(category['option_values'].values()))
        listing.setdefault('direct_category_ids', {})[market] = choice
        listing[market + '_specifics'] = {'description': 'Saved only on ' + market}
        saved = client.put(path + '/listing', headers=alice,
                           json={'listing': listing, 'expected_revision_id': revision})
        assert saved.status_code == 200, saved.text
        revision = saved.json()['revision_id']
        form = client.get(url + '?revision_id=' + revision, headers=alice).json()
        fields = {row['path']: row for row in form['fields']}
        assert fields['direct_category_ids.' + market]['value'] == choice
        assert fields[market + '_specifics.description']['value'] == 'Saved only on ' + market
        assert client.get(url + '?revision_id=stale', headers=alice).status_code == 409
        assert client.post(path + '/native-marketplaces/' + market + '/list', headers=alice,
                           json={'confirm': True}).status_code == 404
    assert client.get(path + '/native-marketplaces/sellwild/form', headers=alice).status_code == 422
    bob_id = client.post('/api/conversations', headers=bob, json={'title': 'Bob coat'}).json()['id']
    bob_path = '/api/conversations/' + bob_id
    assert client.put(bob_path + '/listing', headers=bob, json={
        'listing': {'title': 'Bob coat', 'description': 'Bob private description'}, 'expected_revision_id': '',
    }).status_code == 200
    for market in MARKETS:
        answer = client.get(bob_path + '/native-marketplaces/' + market + '/form', headers=bob)
        assert answer.status_code == 200, answer.text
        field = next(row for row in answer.json()['fields'] if row['path'] == market + '_specifics.description')
        assert field['value'] == 'Bob private description'
        assert 'Saved only on' not in answer.text
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0
        assert db.scalar(select(func.count()).select_from(ListingRevision).where(
            ListingRevision.conversation_id == identity)) == len(MARKETS) + 1

    other = client.post('/api/conversations', headers=alice, json={'title': 'Other coat'}).json()['id']
    response = client.put('/api/conversations/' + other + '/listing', headers=alice,
                          json={'listing': {'title': 'Other private copy'}, 'expected_revision_id': ''})
    assert response.status_code == 200
    with Session(engine) as db:
        db.get(Listing, identity).current_revision_id = response.json()['revision_id']
        db.commit()
    for market in MARKETS:
        refused = client.get(path + '/native-marketplaces/' + market + '/form', headers=alice)
        assert refused.status_code == 409
        assert 'Other private copy' not in refused.text


def test_hosted_ebay_form_reads_the_sellers_own_schema_and_policies(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    from vendoo_studio.services import ebay_listings, marketplace_schemas

    async def schema(marketplace, category_id):
        assert (marketplace, category_id) == ('ebay', '57988')
        return [{'id': 'Brand', 'name': 'Brand', 'required': True, 'usage': 'REQUIRED', 'mode': 'free_text',
                 'multi': False, 'max_values': None, 'max_length': 65, 'values': []}]

    async def profiles(client=None):
        return {'fulfillment': [{'id': 'ship-1', 'title': 'Alice shipping'}], 'payment': [{'id': 'pay-1', 'title': 'Alice payment'}],
                'return': [{'id': 'ret-1', 'title': 'Alice returns'}], 'locations': [{'id': 'loc-1', 'title': 'Alice home'}]}

    monkeypatch.setattr(marketplace_schemas, 'fields', schema)
    monkeypatch.setattr(ebay_listings, 'listing_profiles', profiles)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Coat'}).json()['id']
    path = '/api/conversations/' + identity
    saved = client.put(path + '/listing', headers=alice, json={'listing': {
        'title': 'Wool coat', 'price': 42, 'brand': 'Pendleton', 'direct_category_ids': {'ebay': '57988'}}, 'expected_revision_id': ''})
    assert saved.status_code == 200, saved.text
    url = path + '/native-marketplaces/ebay/form?revision_id=' + saved.json()['revision_id']
    assert client.get(url).status_code == 401
    assert client.get(url, headers=bob).status_code == 404
    response = client.get(url, headers=alice)
    assert response.status_code == 200, response.text
    with use(for_seller(USER_ID)), SellerDatabaseSession(user_id=USER_ID, bind=engine) as db:
        assert response.json() == asyncio.run(editor_form(identity, 'ebay', saved.json()['revision_id'], db))
    fields = {row['path']: row for row in response.json()['fields']}
    assert fields['ebay_specifics.fulfillmentPolicyId']['value'] == 'ship-1'
    assert 'ebay' in client.get('/api/runtime', headers=alice).json()['marketplace_forms']
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0
