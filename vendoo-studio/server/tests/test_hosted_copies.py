from io import BytesIO
import uuid

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.models.conversation import Photo
from vendoo_studio.models.job import Job
from vendoo_studio.services import hosted_photos


def test_copy_and_template_make_private_independent_drafts_with_no_marketplace_jobs(
        cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Original coat'}).json()['id']
    path = '/api/conversations/' + identity
    fields = {'title': 'Original coat', 'brand': 'Pendleton', 'description': 'Private description', 'price': 40}
    assert client.put(path + '/listing', headers=alice,
                      json={'listing': fields, 'expected_revision_id': ''}).status_code == 200
    stored = {}

    def storage(method, key, token, data=None):
        assert key.startswith(USER_ID + '/')
        assert token == alice['Authorization'].partition(' ')[2]
        if method == 'POST':
            stored[key] = data
        elif method == 'DELETE':
            stored.pop(key, None)
        return stored.get(key, b'')

    monkeypatch.setattr(hosted_photos, 'storage_call', storage)
    image = BytesIO()
    Image.new('RGB', (40, 20), 'red').save(image, 'JPEG')
    assert client.post(f'/api/drafts/{identity}/photos', headers=alice, content=image.getvalue()).status_code == 201
    assert client.get(path + '/copy-preview', headers=bob).status_code == 404
    review = client.get(path + '/copy-preview', headers=alice)
    assert review.status_code == 200, review.text
    body = {'request_id': uuid.uuid4().hex, 'expected_basis': review.json()['expected_basis'], 'title': 'Independent copy'}
    copied = client.post(path + '/copy', headers=alice, json=body)
    assert copied.status_code == 200, copied.text
    target = copied.json()['id']
    assert copied.json()['status'] == 'draft'
    assert len(stored) == 2
    assert any(key.startswith(f'{USER_ID}/{target}/') for key in stored)
    assert client.post(path + '/copy', headers=alice, json=body).json()['id'] == target
    assert len(stored) == 2
    with Session(engine) as db:
        assert db.scalar(select(Job.id).limit(1)) is None
        source_photo = db.scalar(select(Photo).where(Photo.conversation_id == identity))
        copied_photo = db.scalar(select(Photo).where(Photo.conversation_id == target))
        assert source_photo.stored_filename != copied_photo.stored_filename
        assert stored[source_photo.stored_filename] == stored[copied_photo.stored_filename]
    source = client.get('/api/listing-templates/source/' + identity, headers=alice).json()
    template = client.post('/api/listing-templates', headers=alice, json={
        'name': 'My coat template', 'selection': {'source_id': identity, 'expected_basis': source['expected_basis'],
                                               'fields': ['brand', 'description']},
    })
    assert template.status_code == 200, template.text
    assert 'My coat template' not in client.get('/api/listing-templates', headers=bob).text
    template_id = template.json()['id']
    created = client.post(f'/api/listing-templates/{template_id}/draft', headers=alice, json={
        'request_id': uuid.uuid4().hex, 'expected_revision': template.json()['revision'], 'title': 'From template',
    })
    assert created.status_code == 200, created.text
    listing = client.get('/api/conversations/' + created.json()['conversation_id'] + '/listing', headers=alice)
    assert listing.json()['listing']['brand'] == 'Pendleton'
