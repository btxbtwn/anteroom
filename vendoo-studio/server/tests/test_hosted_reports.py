from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import jwks, signing_keys  # noqa: F401


def test_reports_exports_boxes_and_calendar_are_account_scoped(cloud, signing_keys):  # noqa: F811
    client, _engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Private Alice coat'}).json()['id']
    saved = client.put(f'/api/conversations/{identity}/listing', headers=alice,
                       json={'listing': {'title': 'Private Alice coat', 'price': 42}, 'expected_revision_id': ''})
    assert saved.status_code == 200, saved.text
    analytics = client.get('/api/analytics', headers=alice)
    assert analytics.status_code == 200, analytics.text
    assert analytics.json()['inventory']['draft'] == 1
    assert client.get('/api/analytics', headers=bob).json()['inventory']['draft'] == 0
    export_path = '/api/sales-ledger/export/inventory.csv'
    assert client.get(export_path).status_code == 401
    exported = client.get(export_path, headers=alice)
    assert exported.status_code == 200, exported.text
    assert 'Private Alice coat' in exported.text
    assert 'Private Alice coat' not in client.get(export_path, headers=bob).text
    boxes = client.post('/api/boxes', headers=alice, json={'store': 'My supplier', 'title': 'Private lot', 'price': 50})
    assert boxes.status_code == 200, boxes.text
    assert 'Private lot' in client.get('/api/boxes', headers=alice).text
    assert 'Private lot' not in client.get('/api/boxes', headers=bob).text
    body = {'title': 'Private sale plan', 'marketplace': 'ebay', 'start_date': '2026-11-01', 'end_date': '2026-11-02',
            'timezone': 'UTC', 'discount_percent': 10, 'fee_percent': 15, 'shipping_cost': 5, 'minimum_profit': 3}
    calendar = client.post('/api/analytics/calendar', headers=alice, json=body)
    assert calendar.status_code == 201, calendar.text
    event_id = calendar.json()['id']
    assert 'Private sale plan' in client.get('/api/analytics/calendar', headers=alice).text
    assert 'Private sale plan' not in client.get('/api/analytics/calendar', headers=bob).text
    assert client.delete('/api/analytics/calendar/' + event_id, headers=bob).status_code == 404
    assert client.delete('/api/analytics/calendar/' + event_id, headers=alice).status_code == 200
