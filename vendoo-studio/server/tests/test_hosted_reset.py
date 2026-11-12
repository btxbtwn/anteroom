"""Web Clear and Regenerate's wipe, and the single price drop bulk Regenerate saves first."""
import io
import json

from PIL import Image
from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.models.background_task import BackgroundTask
from vendoo_studio.models.conversation import Conversation, Message, Photo
from vendoo_studio.models.job import Job
from vendoo_studio.models.listing import ListingRevision
from vendoo_studio.models.marketplace_listing import MarketplaceListing
from vendoo_studio.services import hosted_photos


def test_regenerate_and_clear_wipe_only_owned_idle_items(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    stored = {}

    def storage(method, key, token, data=None):
        assert key.startswith(f'{USER_ID}/')
        if method == 'POST':
            stored[key] = data
        if method == 'DELETE':
            stored.pop(key, None)
        return stored.get(key, b'')

    monkeypatch.setattr(hosted_photos, 'storage_call', storage)
    notes = json.dumps({'titlePinned': True})
    identity = client.post('/api/drafts', headers=alice, json={'title': 'Pinned coat', 'notes': notes}).json()['id']
    path = '/api/conversations/' + identity
    image = io.BytesIO()
    Image.new('RGB', (40, 30), 'red').save(image, 'JPEG')
    assert client.post(f'/api/drafts/{identity}/photos', headers=alice, content=image.getvalue()).status_code < 300
    saved = client.put(path + '/listing', headers=alice, json={
        'listing': {'title': 'Generated coat', 'price': 40, 'sku': 'A23'}, 'expected_revision_id': ''})
    assert saved.status_code == 200, saved.text
    with Session(engine) as db:
        db.add(Message(user_id=USER_ID, conversation_id=identity, role='assistant', text='Old chat'))
        db.commit()

    # The single price drop bulk Regenerate saves before its rewrite.
    preview = client.post(path + '/price-drop/preview', headers=alice)
    assert preview.status_code == 200, preview.text
    assert preview.json()['current_price'] == 40
    assert client.post(path + '/price-drop/preview', headers=bob).status_code == 404
    assert client.post(path + '/price-drop/preview').status_code == 401

    async def no_comps(*args):
        for event in ():
            yield event

    monkeypatch.setattr('vendoo_studio.services.comp_research.stream_sold_comps', no_comps)
    comps = client.post(path + '/price-drop/comps', headers=alice)
    assert comps.status_code == 200, comps.text
    assert 'data: [DONE]' in comps.text
    assert client.post(path + '/price-drop/comps', headers=bob).status_code == 404
    assert client.post(path + '/price-drop/comps').status_code == 401

    price_body = {'price': 30, 'mode': 'custom', 'expected_revision_id': preview.json()['revision_id']}
    assert client.post(path + '/price-drop', headers=bob, json=price_body).status_code == 404
    assert client.post(path + '/price-drop', headers=alice, json={'price': 30}).status_code == 422
    dropped = client.post(path + '/price-drop', headers=alice, json=price_body)
    assert dropped.status_code == 200, dropped.text
    assert client.get(path + '/listing', headers=alice).json()['listing']['price'] == 30
    assert client.post(path + '/price-drop', headers=alice, json={**price_body, 'price': 20}).status_code == 409
    assert client.get(path + '/listing', headers=alice).json()['listing']['price'] == 30

    # The bulk review shows what Regenerate keeps from the listing, without saving it.
    details = path + '/regenerate-details'
    assert client.get(details).status_code == 401
    assert client.get(details, headers=bob).status_code == 404
    shown = client.get(details, headers=alice)
    assert shown.status_code == 200, shown.text
    assert json.loads(shown.json()['notes']) == {'titlePinned': True, 'sku': 'A23', 'askingPrice': '30.00'}
    assert client.get(path, headers=alice).json()['notes'] == notes

    body = {'keep_inputs': True}
    assert client.post(path + '/reset', json=body).status_code == 401
    assert client.post(path + '/reset', headers=bob, json=body).status_code == 404
    with Session(engine) as db:
        task = BackgroundTask(user_id=USER_ID, conversation_id=identity, kind='generate', payload={})
        db.add(task)
        db.commit()
        task_id = task.id
    assert client.post(path + '/reset', headers=alice, json=body).status_code == 409
    with Session(engine) as db:
        db.get(BackgroundTask, task_id).status = 'cancelled'
        db.commit()

    regenerated = client.post(path + '/reset', headers=alice, json=body)
    assert regenerated.status_code == 200, regenerated.text
    assert regenerated.json()['title'] == 'Generated coat'
    with Session(engine) as db:
        assert db.query(ListingRevision).filter_by(conversation_id=identity).count() == 0
        assert db.query(Message).filter_by(conversation_id=identity).count() == 0
        assert db.query(BackgroundTask).filter_by(conversation_id=identity).count() == 0
        assert db.query(Photo).filter_by(conversation_id=identity).count() == 1
        assert json.loads(db.get(Conversation, identity).notes)['titlePinned'] is True
    assert stored

    cleared = client.post(path + '/reset', headers=alice, json={'keep_inputs': False})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()['title'] == 'New Listing' and cleared.json()['notes'] is None
    assert not stored
    with Session(engine) as db:
        assert db.query(Photo).filter_by(conversation_id=identity).count() == 0
        db.get(Conversation, identity).status = 'active'
        db.commit()
    assert client.post(path + '/reset', headers=alice, json={'keep_inputs': False}).status_code == 409
    with Session(engine) as db:
        assert db.query(Job).count() == 0


def test_regenerate_rewrites_a_listed_item_and_keeps_its_marketplace_records(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    alice = headers(signing_keys)
    monkeypatch.setattr(hosted_photos, 'storage_call', lambda *args, **kwargs: b'')
    identity = client.post('/api/drafts', headers=alice, json={'title': 'Listed tee'}).json()['id']
    path = '/api/conversations/' + identity
    saved = client.put(path + '/listing', headers=alice, json={
        'listing': {'title': 'Listed tee', 'price': 20}, 'expected_revision_id': ''})
    assert saved.status_code == 200, saved.text
    with Session(engine) as db:
        db.get(Conversation, identity).status = 'active'
        job = Job(user_id=USER_ID, conversation_id=identity, approved_revision_id='r', listing_snapshot={},
                  status='dispatched')
        db.add(job)
        db.flush()
        db.add(MarketplaceListing(user_id=USER_ID, conversation_id=identity, marketplace='ebay', state='published',
                                  listing_id='live-1', job_id=job.id))
        db.commit()
        job_id = job.id

    # A marketplace action still writing this item has to finish first.
    busy = client.post(path + '/reset', headers=alice, json={'keep_inputs': True})
    assert busy.status_code == 409
    with Session(engine) as db:
        db.get(Job, job_id).status = 'completed'
        db.commit()

    regenerated = client.post(path + '/reset', headers=alice, json={'keep_inputs': True})
    assert regenerated.status_code == 200, regenerated.text
    with Session(engine) as db:
        assert db.query(ListingRevision).filter_by(conversation_id=identity).count() == 0
        assert db.query(MarketplaceListing).filter_by(conversation_id=identity).count() == 1
        assert db.get(Job, job_id) is not None

    # Clear still refuses an item with marketplace records: it would drop its photos.
    assert client.post(path + '/reset', headers=alice, json={'keep_inputs': False}).status_code == 409
