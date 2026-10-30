import uuid

from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.models.background_task import BackgroundTask
from vendoo_studio.models.job import Job


def test_reviewed_bulk_prices_and_copies_are_owned_stale_checked_and_draft_only(cloud, signing_keys):  # noqa: F811
    client, engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)

    def item(auth, title, price):
        identity = client.post('/api/conversations', headers=auth, json={'title': title}).json()['id']
        saved = client.put('/api/conversations/' + identity + '/listing', headers=auth, json={
            'listing': {'title': title, 'price': price}, 'expected_revision_id': '',
        })
        assert saved.status_code == 200, saved.text
        return identity, saved.json()['revision_id']

    first, revision = item(alice, 'Alice coat', 40)
    second, second_revision = item(alice, 'Alice jacket', 60)
    foreign, foreign_revision = item(bob, 'Bob private title', 70)
    endpoint = '/api/price-drop/batch'
    runtime = client.get('/api/runtime', headers=alice).json()['features']
    assert {'bulk_actions', 'bulk_copy', 'bulk_markdowns'} <= set(runtime)
    assert not {'bulk_delete', 'bulk_regenerate', 'marketplace_actions'} & set(runtime)
    assert client.post(endpoint + '/preview', json={'conversation_ids': [first]}).status_code == 401
    assert client.post(endpoint + '/preview', headers=alice, json={'conversation_ids': [foreign]}).status_code == 404
    preview = client.post(endpoint + '/preview', headers=alice, json={'conversation_ids': [first, second]})
    assert preview.status_code == 200, preview.text
    assert [row['current_price'] for row in preview.json()['rows']] == [40, 60]
    drop = {'conversation_id': first, 'price': 30, 'expected_revision_id': revision}
    mixed = client.post(endpoint, headers=alice, json={'drops': [drop, {
        'conversation_id': foreign, 'price': 50, 'expected_revision_id': foreign_revision,
    }]})
    assert mixed.status_code == 404
    assert client.get('/api/conversations/' + first + '/listing', headers=alice).json()['listing']['price'] == 40
    assert client.post(endpoint, headers=alice, json={'drops': [{'conversation_id': first, 'price': 30}]}).status_code == 422
    assert client.post(endpoint, headers=alice, json={'drops': [{**drop, 'price': 1000001}]}).status_code == 422
    changed = client.post(endpoint, headers=alice, json={'drops': [drop, drop]})
    assert changed.status_code == 200, changed.text
    assert len(changed.json()['applied']) == 1
    assert client.get('/api/conversations/' + first + '/listing', headers=alice).json()['listing']['price'] == 30
    assert client.post(endpoint, headers=alice, json={'drops': [{**drop, 'price': 20}]}).json()['applied'] == []
    with Session(engine) as db:
        task = BackgroundTask(user_id=USER_ID, conversation_id=second, kind='chat', payload={})
        db.add(task)
        db.commit()
        task_id = task.id
    queued_drop = {'conversation_id': second, 'price': 50, 'expected_revision_id': second_revision}
    busy = client.post(endpoint + '/preview', headers=alice, json={'conversation_ids': [second]}).json()['rows'][0]
    assert busy['skip_reason']
    assert client.post(endpoint, headers=alice, json={'drops': [queued_drop]}).json()['applied'] == []
    copy_path = '/api/listings/copies/preview'
    copies = client.post(copy_path, headers=alice, json={'conversation_ids': [first, second, foreign]}).json()['rows']
    assert copies[0]['expected_basis']
    assert copies[1]['skip_reason']
    assert copies[2]['skip_reason']
    assert 'Bob private title' not in str(copies)
    copied = client.post('/api/conversations/' + first + '/copy', headers=alice, json={
        'request_id': uuid.uuid4().hex, 'expected_basis': copies[0]['expected_basis'],
        'title': 'Coat draft copy', 'include_photos': False,
    })
    assert copied.status_code == 200, copied.text
    target = copied.json()['id']
    assert copied.json()['status'] == 'draft'
    assert client.get('/api/conversations/' + target, headers=bob).status_code == 404
    with Session(engine) as db:
        db.get(BackgroundTask, task_id).status = 'cancelled'
        db.commit()
    assert len(client.post(endpoint, headers=alice, json={'drops': [queued_drop]}).json()['applied']) == 1
    assert client.get('/api/conversations/' + foreign + '/listing', headers=bob).json()['listing']['price'] == 70
    with Session(engine) as db:
        assert db.query(Job).count() == 0
