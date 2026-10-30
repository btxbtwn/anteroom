import json

from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.models.background_task import BackgroundTask
from vendoo_studio.models.job import Job


def test_reviewed_account_bulk_edits_reject_foreign_stale_and_busy_items(cloud, signing_keys):  # noqa: F811
    client, engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)

    def item(auth, title):
        identity = client.post('/api/conversations', headers=auth, json={'title': title}).json()['id']
        response = client.put('/api/conversations/' + identity + '/listing', headers=auth, json={
            'listing': {'title': title, 'price': 40}, 'expected_revision_id': '',
        })
        assert response.status_code == 200
        return identity

    first, foreign = item(alice, 'Alice coat'), item(bob, 'Bob private coat')
    endpoint = '/api/listings/bulk-edit'
    edits = {'values': {'price': 30}, 'labels_mode': 'add', 'labels': ['Sale']}
    request = {'conversation_ids': [first, foreign], 'edits': edits}
    assert client.post(endpoint + '/preview', json=request).status_code == 401
    preview = client.post(endpoint + '/preview', headers=alice, json=request)
    assert preview.status_code == 200, preview.text
    assert 'Bob private coat' not in preview.text
    rows = preview.json()['rows']
    assert rows[0]['changes'] and not rows[0]['skip_reason']
    assert rows[1]['skip_reason']
    fields = ('conversation_id', 'expected_revision_id', 'expected_basis', 'expected_result')
    reviewed = {key: rows[0][key] for key in fields}
    body = {'edits': edits, 'rows': [reviewed]}
    assert client.post(endpoint + '/apply', headers=bob, json=body).json()['applied'] == []
    tampered = {'edits': edits, 'rows': [{**reviewed, 'expected_result': '0' * 64}]}
    assert client.post(endpoint + '/apply', headers=alice, json=tampered).json()['applied'] == []
    with Session(engine) as db:
        task = BackgroundTask(user_id=USER_ID, conversation_id=first, kind='chat', payload={})
        db.add(task)
        db.commit()
        task_id = task.id
    busy = client.post(endpoint + '/apply', headers=alice, json=body)
    assert busy.status_code == 200, busy.text
    assert busy.json()['applied'] == [] and busy.json()['skipped']
    assert client.get('/api/conversations/' + first + '/listing', headers=alice).json()['listing']['price'] == 40
    with Session(engine) as db:
        db.get(BackgroundTask, task_id).status = 'cancelled'
        db.commit()
    changed = client.post(endpoint + '/apply', headers=alice, json=body)
    assert changed.status_code == 200, changed.text
    assert len(changed.json()['applied']) == 1
    response = client.get('/api/conversations/' + first + '/listing', headers=alice).json()
    saved = response['listing']
    assert saved['price'] == 30 and 'Sale' in saved['labels']
    notes = client.get('/api/conversations/' + first, headers=alice).json()['notes']
    assert 'Sale' in json.loads(notes)['vendooLabels']
    # A later normal editor save must retain the bulk labels in its payload.
    assert client.put('/api/conversations/' + first + '/listing', headers=alice, json={
        'listing': {**saved, 'description': 'Edited after the batch'},
        'expected_revision_id': response['current_revision_id'],
    }).status_code == 200
    assert client.get('/api/conversations/' + first + '/listing', headers=alice).json()['listing']['labels'] == ['Sale']
    assert client.post(endpoint + '/apply', headers=alice, json=body).json()['applied'] == []
    assert client.get('/api/conversations/' + foreign + '/listing', headers=bob).json()['listing']['price'] == 40
    categories = {**request, 'edits': {'category': {'ebay': {'id': '123', 'path': 'Unverified category'}}}}
    assert client.post(endpoint + '/preview', headers=alice, json=categories).status_code == 400
    assert client.get('/api/catalog/dropdown-options').status_code == 401
    dropdowns = client.get('/api/catalog/dropdown-options', headers=alice)
    assert dropdowns.status_code == 200, dropdowns.text
    assert 'primaryColor' in dropdowns.json()['forms']['vendoo']
    features = client.get('/api/runtime', headers=alice).json()['features']
    assert 'bulk_edit' in features and 'catalog_categories' not in features
    with Session(engine) as db:
        assert db.query(Job).count() == 0
