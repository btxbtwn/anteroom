import io
import json

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.models.background_task import BackgroundTask
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.job import Job
from vendoo_studio.models.similar_item import PhotoFingerprint
from vendoo_studio.services import hosted_photos


def test_inventory_label_controls_persist_in_the_account(cloud, signing_keys):  # noqa: F811
    client, _engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    path = '/api/settings/ui'
    before = client.get(path, headers=bob).json()
    assert client.put(path, headers=alice, json={'remember_labels': ['Closet']}).status_code == 200
    assert 'Closet' in client.get(path, headers=alice).json()['recent_vendoo_labels']
    assert client.put(path, headers=alice, json={'label_colors': {'Closet': 'teal'}, 'stale_listing_days': 45}).status_code == 200
    saved = client.get(path, headers=alice).json()
    assert saved['label_colors']['Closet'] == 'teal'
    assert saved['stale_listing_days'] == 45
    assert client.put(path, headers=alice, json={'forget_label': 'Closet'}).status_code == 200
    assert 'Closet' in client.get(path, headers=alice).json()['hidden_vendoo_labels']
    assert client.put(path, headers=alice, json={'remember_labels': ['Closet'], 'restore_labels': ['Closet']}).status_code == 200
    assert 'Closet' not in client.get(path, headers=alice).json()['hidden_vendoo_labels']
    assert client.put(path, headers=alice, json={'vendoo_enabled': True}).status_code == 422
    assert client.put(path, headers=alice, json={'forget_label': ''}).status_code == 400
    assert client.get(path, headers=bob).json() == before


def test_similar_photos_reviews_and_merges_stay_with_the_owner(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    owners = {alice['Authorization'].split()[1]: USER_ID, bob['Authorization'].split()[1]: BOB}
    stored = {}

    def storage(method, key, token, data=None):
        assert key.startswith(owners[token] + '/')
        if method == 'POST':
            stored[key] = data
        return stored.get(key, b'')

    monkeypatch.setattr(hosted_photos, 'storage_call', storage)
    image = io.BytesIO()
    Image.new('RGB', (80, 120), 'red').save(image, 'JPEG')

    def item(auth, title):
        identity = client.post('/api/conversations', headers=auth, json={'title': title}).json()['id']
        uploaded = client.post('/api/drafts/' + identity + '/photos', headers=auth, content=image.getvalue())
        assert uploaded.status_code == 201, uploaded.text
        return identity

    first, second = item(alice, 'Alice coat'), item(alice, 'Alice jacket')
    third, fourth = item(bob, 'Bob coat'), item(bob, 'Bob jacket')
    endpoint = '/api/similar-items'
    assert client.get(endpoint).status_code == 401
    for auth, ids in ((alice, {first, second}), (bob, {third, fourth})):
        response = client.get(endpoint, headers=auth)
        assert response.status_code == 200, response.text
        pairs = response.json()['pairs']
        assert len(pairs) == 1
        assert {pairs[0]['first']['id'], pairs[0]['second']['id']} == ids
        assert 'same_photo' in pairs[0]['reasons']
    with Session(engine) as db:
        assert set(db.scalars(select(PhotoFingerprint.user_id))) == {USER_ID, BOB}
    assert client.post(endpoint + '/dismiss', headers=bob, json={'first_id': first, 'second_id': fourth}).status_code == 404
    assert client.post(endpoint + '/merge', headers=bob, json={'keep_id': first, 'merge_id': fourth}).status_code == 404
    assert client.post(endpoint + '/dismiss', headers=alice, json={'first_id': first, 'second_id': second}).status_code == 200
    assert client.get(endpoint, headers=alice).json()['pairs'] == []
    assert len(client.get(endpoint, headers=bob).json()['pairs']) == 1
    merged = client.post(endpoint + '/merge', headers=bob, json={'keep_id': third, 'merge_id': fourth})
    assert merged.status_code == 200, merged.text
    assert client.get(endpoint, headers=bob).json()['pairs'] == []
    with Session(engine) as db:
        assert json.loads(db.get(Conversation, fourth).notes)['mergedInto'] == third
        assert not db.get(Conversation, second).notes
        task = BackgroundTask(user_id=USER_ID, conversation_id=second, kind='chat', payload={})
        db.add(task)
        db.commit()
        task_id = task.id
    body = {'keep_id': first, 'merge_id': second}
    assert client.post(endpoint + '/merge', headers=alice, json=body).status_code == 409
    with Session(engine) as db:
        db.get(BackgroundTask, task_id).status = 'cancelled'
        db.commit()
    assert client.post(endpoint + '/merge', headers=alice, json=body).status_code == 200
    with Session(engine) as db:
        assert db.query(Job).count() == 0
        assert db.query(Conversation).count() == 4
