from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.models.background_task import BackgroundTask
from vendoo_studio.models.conversation import Conversation


def test_reviewed_bulk_delete_removes_only_owned_unsent_idle_drafts(cloud, signing_keys):  # noqa: F811
    client, engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)

    def item(auth, title):
        return client.post('/api/conversations', headers=auth, json={'title': title}).json()['id']

    first, busy, sent, stale = (item(alice, name) for name in ('First', 'Busy', 'Sent', 'Stale'))
    foreign = item(bob, 'Bob private coat')
    with Session(engine) as db:
        db.get(Conversation, sent).status = 'active'
        task = BackgroundTask(user_id=USER_ID, conversation_id=busy, kind='chat', payload={})
        db.add(task)
        db.commit()
        task_id = task.id
    endpoint = '/api/listings/bulk-delete'
    request = {'conversation_ids': [first, busy, sent, stale, foreign]}
    assert client.post(endpoint + '/preview', json=request).status_code == 401
    preview = client.post(endpoint + '/preview', headers=alice, json=request)
    assert preview.status_code == 200, preview.text
    assert 'Bob private coat' not in preview.text
    rows = {row['conversation_id']: row for row in preview.json()['rows']}
    assert rows[first]['review_token'] and not rows[first]['skip_reason']
    assert rows[stale]['review_token']
    for skipped in (busy, sent, foreign):
        assert rows[skipped]['skip_reason'] and rows[skipped]['review_token'] is None
    reviewed = [{'conversation_id': key, 'review_token': rows[key]['review_token']} for key in (first, stale)]
    assert client.post(endpoint + '/delete', headers=alice, json={'rows': reviewed}).status_code == 400
    assert client.post(endpoint + '/delete', headers=bob, json={'rows': reviewed, 'confirm': True}).json()['deleted'] == []
    assert client.patch('/api/conversations/' + stale, headers=alice, json={'title': 'Renamed'}).status_code == 200
    with Session(engine) as db:
        db.get(BackgroundTask, task_id).status = 'cancelled'
        db.commit()
    forged = [*reviewed, {'conversation_id': busy, 'review_token': '0' * 64}]
    result = client.post(endpoint + '/delete', headers=alice, json={'rows': forged, 'confirm': True})
    assert result.status_code == 200, result.text
    assert result.json()['deleted'] == [first]
    assert {row['conversation_id'] for row in result.json()['skipped']} == {stale, busy}
    assert client.get('/api/conversations/' + first, headers=alice).status_code == 404
    for kept in (busy, sent, stale):
        assert client.get('/api/conversations/' + kept, headers=alice).status_code == 200
    assert client.get('/api/conversations/' + foreign, headers=bob).status_code == 200
    assert 'bulk_delete' in client.get('/api/runtime', headers=alice).json()['features']
