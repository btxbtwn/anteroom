from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import jwks, signing_keys  # noqa: F401


def test_listing_preferences_persist_without_changing_another_seller(cloud, signing_keys):  # noqa: F811
    client, _engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    paths = ('marketplaces', 'package-dimensions', 'ships-from-zip', 'listing-defaults',
             'ebay-inventory-location', 'formulas', 'hidden-fields')
    before = {path: client.get('/api/settings/' + path, headers=bob).json() for path in paths}
    markets = client.get('/api/settings/marketplaces', headers=alice).json()
    assert {'poshmark', 'depop'} <= {row['id'] for row in markets['available']}
    assert 'depop' in markets['selected']
    result = client.put('/api/settings/marketplaces', headers=alice, json={'selected': ['depop', 'poshmark']})
    assert result.status_code == 200, result.text
    assert result.json()['selected'] == ['poshmark', 'depop']
    assert client.get('/api/settings/marketplaces', headers=alice).json()['selected'] == ['poshmark', 'depop']
    assert client.put('/api/settings/marketplaces', headers=alice, json={'selected': ['not-a-market']}).status_code == 400
    dimensions = {'length': 12, 'width': 10, 'height': 3}
    assert client.put('/api/settings/package-dimensions', headers=alice, json=dimensions).status_code == 200
    assert client.get('/api/settings/package-dimensions', headers=alice).json() == dimensions
    assert client.put('/api/settings/ships-from-zip', headers=alice, json={'zip': '70125'}).status_code == 200
    default = {'path': 'mercari_specifics.shipsFrom', 'value': '70126', 'label': 'Ships from'}
    result = client.put('/api/settings/listing-defaults', headers=alice, json=default)
    assert result.status_code == 200, result.text
    assert result.json()['defaults']['mercari_specifics.shipsFrom']['value'] == '70126'
    assert client.get('/api/settings/ships-from-zip', headers=alice).json()['zip'] == '70126'
    assert client.delete('/api/settings/listing-defaults?path=mercari_specifics.shipsFrom', headers=alice).status_code == 200
    assert 'mercari_specifics.shipsFrom' not in client.get('/api/settings/listing-defaults', headers=alice).json()['defaults']
    assert client.put('/api/settings/ebay-inventory-location', headers=alice, json={'location': '70125'}).status_code == 200
    assert client.get('/api/settings/ebay-inventory-location', headers=alice).json()['location'] == '70125'
    formulas = client.get('/api/settings/formulas', headers=alice).json()
    custom = {'title': formulas['default_title'] + ' Custom', 'description': formulas['default_description']}
    response = client.put('/api/settings/formulas', headers=alice, json=custom)
    assert response.status_code == 200, response.text
    assert client.get('/api/settings/formulas', headers=alice).json()['title'] == custom['title']
    after = {path: client.get('/api/settings/' + path, headers=bob).json() for path in paths}
    assert before == after
    for path in paths:
        assert client.get('/api/settings/' + path).status_code == 401


def test_hidden_field_changes_require_the_listing_owner_and_restore_the_saved_choices(cloud, signing_keys):  # noqa: F811
    client, _engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Private coat'}).json()['id']
    endpoint = '/api/settings/hidden-fields'
    body = {'marketplace': 'poshmark', 'field': 'Style tags', 'label': 'Style tags',
            'scope': 'listing', 'conversation_id': identity}
    for method in ('PUT', 'DELETE'):
        assert client.request(method, endpoint, headers=bob, json=body).status_code == 404
    for suffix, payload in (('/restore-all', {'conversation_id': identity}),
                            ('/restore-matching', {'conversation_id': identity, 'fields': []})):
        assert client.post(endpoint + suffix, headers=bob, json=payload).status_code == 404
    assert client.get(endpoint + '?conversation_id=' + identity, headers=bob).status_code == 404
    assert client.put(endpoint, headers=alice, json=body).status_code == 200
    saved = client.get(endpoint + '?conversation_id=' + identity, headers=alice).json()
    assert saved['listing'][0]['field'] == 'style tags'
    assert client.post(endpoint + '/restore-matching', headers=alice, json={
        'conversation_id': identity, 'fields': [{'marketplace': 'poshmark', 'field': 'Style tags'}],
    }).json()['listing'] == []
    always = {**body, 'scope': 'always', 'conversation_id': None}
    assert client.put(endpoint, headers=alice, json=always).status_code == 200
    assert client.request('DELETE', endpoint, headers=alice, json=always).json()['always'] == []
    assert client.put(endpoint, headers=alice, json=always).status_code == 200
    assert client.post(endpoint + '/restore-all', headers=alice, json={}).json()['always'] == []
    assert client.get(endpoint, headers=bob).json() == {'always': [], 'listing': []}
