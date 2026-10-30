"""The shared editor's actual HTTP contracts run under two seller identities."""
import io

from PIL import Image
from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.models.conversation import Photo
from vendoo_studio.models.listing import Listing, ListingRevision
from vendoo_studio.services import hosted_photos


def test_full_editor_saves_history_and_preserves_existing_draft(cloud, signing_keys):  # noqa: F811
    client, engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    old = client.post('/api/drafts', headers=alice, json={'title': 'Coat', 'notes': 'Keep every measurement'}).json()
    path = '/api/conversations/' + old['id']
    assert client.get(path, headers=alice).json()['notes'] == 'Keep every measurement'
    assert client.get('/api/conversations', headers=bob).json() == []
    for suffix in ['', '/listing', '/messages', '/activity', '/photos', '/revisions', '/marketplace-listings']:
        assert client.get(path + suffix, headers=bob).status_code == 404
        assert client.get(path + suffix).status_code == 401
    fields = {'title': 'Wool coat', 'description': 'Checked', 'price': 42, 'quantity': 2, 'brand': 'Pendleton'}
    saved = client.put(path + '/listing', headers=alice, json={'listing': fields, 'expected_revision_id': ''})
    assert saved.status_code == 200, saved.text
    summary = client.get('/api/conversations', headers=alice).json()[0]
    assert summary['price'] == 42
    assert summary['quantity'] == 2
    assert 'notes' not in summary
    first = saved.json()['revision_id']
    assert saved.json()['listing'] == fields
    assert client.put(path + '/listing', headers=alice, json={'listing': fields, 'expected_revision_id': ''}).status_code == 409
    second = client.put(path + '/listing', headers=alice, json={'listing': {**fields, 'price': 44}, 'expected_revision_id': first}).json()['revision_id']
    history = client.get(path + '/revisions', headers=alice).json()
    assert len(history) == 2
    restored = client.post(path + '/revisions/' + first + '/restore', headers=alice, json={'expected_revision_id': second})
    assert restored.status_code == 200, restored.text
    assert client.get(path + '/listing', headers=alice).json()['listing']['price'] == 42
    assert client.get(path + '/revisions/' + first, headers=bob).status_code == 404
    with Session(engine) as db:
        row = db.get(ListingRevision, first)
        assert row.user_id == USER_ID
        assert row.inventory_quantity == 2
    assert client.get(path, headers=alice).json()['notes'] == 'Keep every measurement'


def test_revision_and_operational_id_injection_is_refused(cloud, signing_keys):  # noqa: F811
    client, engine = cloud
    alice = headers(signing_keys)
    first = client.post('/api/conversations', headers=alice, json={'title': 'One'}).json()['id']
    second = client.post('/api/conversations', headers=alice, json={'title': 'Two'}).json()['id']
    path = '/api/conversations/' + first
    for fields in [{'user_id': BOB}, {'ebay_specifics': {'listing_id': 'another'}}, {'direct_category_ids': {'user_id': BOB}}, {'poshmark_specifics': {'cookie': 'secret'}}]:
        assert client.put(path + '/listing', headers=alice, json={'listing': fields, 'expected_revision_id': ''}).status_code == 422
    saved = client.put('/api/conversations/' + second + '/listing', headers=alice, json={'listing': {'title': 'Other'}, 'expected_revision_id': ''}).json()
    revision_id = saved['revision_id']
    assert client.get(path + '/revisions/' + revision_id, headers=alice).status_code == 404
    assert client.post(path + '/revisions/' + revision_id + '/restore', headers=alice, json={'expected_revision_id': ''}).status_code == 404
    # Legacy soft references cannot expose another conversation's revision,
    # even when it belongs to the same seller.
    with Session(engine) as db:
        db.add(Listing(user_id=USER_ID, conversation_id=first, current_revision_id=revision_id))
        db.commit()
    assert client.get(path + '/listing', headers=alice).json()['listing']['title'] == 'One'
    assert client.put(path + '/listing', headers=alice, json={'listing': {'title': 'Changed'}, 'expected_revision_id': revision_id}).status_code == 409


def test_account_preferences_do_not_share_settings(cloud, signing_keys):  # noqa: F811
    client, _ = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    assert client.put('/api/settings/ui', headers=alice, json={'theme': 'light'}).status_code == 200
    assert client.put('/api/settings/ui', headers=alice, json={'listing_names': 'sku'}).status_code == 200
    assert client.get('/api/settings/ui', headers=alice).json()['theme'] == 'light'
    assert client.get('/api/settings/ui', headers=bob).json()['theme'] == 'dark'
    assert client.put('/api/settings/ui', headers=alice, json={'vendoo_enabled': True}).status_code == 422
    assert client.put('/api/settings/ui', headers=alice, json={'user_id': BOB}).status_code == 422


def test_shared_photo_contract_remains_private(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Coat'}).json()['id']
    stored = {}
    def storage(method, key, token, data=None):
        assert key.startswith(USER_ID + '/' + identity + '/')
        assert token == alice['Authorization'].partition(' ')[2]
        if method == 'POST':
            stored[key] = data
        if method == 'DELETE':
            stored.pop(key, None)
        return stored.get(key, b'')
    monkeypatch.setattr(hosted_photos, 'storage_call', storage)
    image = io.BytesIO()
    Image.new('RGB', (200, 100), 'red').save(image, 'JPEG')
    uploaded = client.post('/api/drafts/' + identity + '/photos', headers=alice, content=image.getvalue())
    assert uploaded.status_code == 201, uploaded.text
    photo = uploaded.json()['id']
    path = '/api/conversations/' + identity + '/photos'
    cover_url = client.get('/api/conversations', headers=alice).json()[0]['cover_photo_url']
    assert cover_url.startswith('/api/photos/' + photo + '/thumb?size=96&v=')
    rows = client.get(path, headers=alice).json()
    assert rows[0]['id'] == photo
    selection = {'title': 'My coat', 'marketplace_photos': {'poshmark': [photo]}}
    saved = client.put('/api/conversations/' + identity + '/listing', headers=alice,
                       json={'listing': selection, 'expected_revision_id': ''})
    assert saved.status_code == 200, saved.text
    other = client.post('/api/conversations', headers=bob, json={'title': 'Bob item'}).json()['id']
    assert client.put('/api/conversations/' + other + '/listing', headers=bob,
                      json={'listing': selection, 'expected_revision_id': ''}).status_code == 422
    assert client.get(rows[0]['url'], headers=alice).status_code == 200
    assert client.get(rows[0]['url'], headers=bob).status_code == 404
    assert client.get('/api/photos/' + photo + '/thumb', headers=bob).status_code == 404
    thumb = client.get('/api/photos/' + photo + '/thumb?size=96', headers=alice)
    assert thumb.status_code == 200
    with Image.open(io.BytesIO(thumb.content)) as result:
        assert result.size == (96, 48)
    assert client.patch(path + '/order', headers=alice, json=[photo]).status_code == 200
    assert client.patch(path + '/order', headers=alice, json=[photo, photo]).status_code == 422
    assert client.patch(path + '/order', headers=bob, json=[photo]).status_code == 404
    assert client.delete(path + '/' + photo, headers=bob).status_code == 404
    assert client.delete(path + '/' + photo, headers=alice).status_code == 200
    listing_path = '/api/conversations/' + identity + '/listing'
    remaining = client.get(listing_path, headers=alice).json()
    assert remaining['listing']['marketplace_photos']['poshmark'] == []
    assert client.put(listing_path, headers=alice, json={'listing': remaining['listing'],
        'expected_revision_id': remaining['current_revision_id']}).status_code == 200
    with Session(engine) as db:
        assert db.get(Photo, photo) is None
    assert stored == {}


def test_photo_edits_preserve_private_original_and_reset_after_reload(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Photo editing'}).json()['id']
    stored = {}

    def storage(method, key, token, data=None):
        assert key.startswith(f'{USER_ID}/{identity}/')
        assert token == alice['Authorization'].partition(' ')[2]
        if method == 'POST':
            stored[key] = data
        if method == 'DELETE':
            stored.pop(key, None)
        return stored.get(key, b'')

    monkeypatch.setattr(hosted_photos, 'storage_call', storage)
    image = io.BytesIO()
    Image.new('RGB', (200, 100), 'red').save(image, 'JPEG')
    uploaded = client.post(f'/api/drafts/{identity}/photos', headers=alice, content=image.getvalue()).json()
    photo_id = uploaded['id']
    path = f'/api/conversations/{identity}/photos/{photo_id}'
    original_key = next(iter(stored))
    assert client.post(path + '/rotate', headers=bob, json={'degrees': 90}).status_code == 404
    rotated = client.post(path + '/rotate', headers=alice, json={'degrees': 90})
    assert rotated.status_code == 200, rotated.text
    assert (rotated.json()['width'], rotated.json()['height']) == (100, 200)
    assert len(stored) == 2
    assert original_key in stored
    url = client.get(f'/api/conversations/{identity}/photos', headers=alice).json()[0]['url']
    assert '?v=' in url
    with Image.open(io.BytesIO(client.get(url, headers=alice).content)) as result:
        assert result.size == (100, 200)
    assert client.get(rotated.json()['url'], headers=bob).status_code == 404
    assert client.post(path + '/edits/preview', headers=bob, json={'brightness': 10}).status_code == 404
    preview = client.post(path + '/edits/preview', headers=alice, json={'brightness': 10})
    assert preview.status_code == 200, preview.text
    reset = client.delete(path + '/edits', headers=alice)
    assert reset.status_code == 200, reset.text
    assert (reset.json()['width'], reset.json()['height']) == (200, 100)
    assert list(stored) == [original_key]
    with Session(engine) as db:
        photo = db.get(Photo, photo_id)
        assert photo.source_filename is None
        assert photo.stored_filename == original_key


def test_heic_and_marketplace_photos_keep_the_desktop_limits_independent(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    alice = headers(signing_keys)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'HEIC photos'}).json()['id']
    with Session(engine) as db:
        db.add_all(Photo(user_id=USER_ID, conversation_id=identity, original_filename='item.jpg',
                         stored_filename=f'{USER_ID}/{identity}/{index:032x}.jpg',
                         mime_type='image/jpeg', size_bytes=1, display_order=index)
                   for index in range(hosted_photos.MAX_PHOTOS))
        db.commit()
    stored = {}
    def storage(method, key, token, data=None):
        assert key.startswith(f'{USER_ID}/{identity}/')
        assert token == alice['Authorization'].partition(' ')[2]
        if method == 'POST':
            stored[key] = data
        return stored.get(key, b'')
    monkeypatch.setattr(hosted_photos, 'storage_call', storage)
    image, exif = io.BytesIO(), Image.Exif()
    exif[0x0112] = 6
    Image.new('RGB', (120, 80), 'red').save(image, 'HEIF', exif=exif.tobytes())
    path = f'/api/drafts/{identity}/photos'
    assert client.post(path, headers=alice, content=image.getvalue()).status_code == 422
    uploaded = client.post(path + '?marketplace=ebay', headers=alice, content=image.getvalue())
    assert uploaded.status_code == 201, uploaded.text
    assert (uploaded.json()['width'], uploaded.json()['height']) == (80, 120)
    with Image.open(io.BytesIO(next(iter(stored.values())))) as result:
        assert result.format == 'JPEG'
        assert not result.getexif()
    assert len(client.get(f'/api/conversations/{identity}/photos', headers=alice).json()) == hosted_photos.MAX_PHOTOS
    assert len(client.get(f'/api/conversations/{identity}/photos?marketplace=ebay', headers=alice).json()) == 1
    invalid_pick = {'title': 'HEIC photos', 'marketplace_photos': {'poshmark': [uploaded.json()['id']]}}
    assert client.put(f'/api/conversations/{identity}/listing', headers=alice,
                      json={'listing': invalid_pick, 'expected_revision_id': ''}).status_code == 422


def test_delete_only_removes_the_sellers_unsent_draft(cloud, signing_keys):  # noqa: F811
    client, _ = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Remove me'}).json()['id']
    path = '/api/conversations/' + identity
    assert client.delete(path, headers=bob).status_code == 404
    assert client.get(path, headers=alice).status_code == 200
    assert client.delete(path, headers=alice).json() == {'ok': True, 'deleted_jobs': 0, 'deleted_photos': 0}
    assert client.get(path, headers=alice).status_code == 404
