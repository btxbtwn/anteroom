"""The shared editor's actual HTTP contracts run under two seller identities."""
import io

import pytest
from PIL import Image
from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_hosted_native_forms import with_choice_lists
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.models.conversation import Photo
from vendoo_studio.models.listing import Listing, ListingRevision
from vendoo_studio.services import hosted_photos


def test_manual_general_changes_follow_across_marketplaces_and_keep_own_edits(cloud, signing_keys):  # noqa: F811
    from vendoo_studio.services.seller_overrides import MARKETPLACES

    client, _engine = cloud
    auth = headers(signing_keys)
    identity = client.post('/api/conversations', headers=auth, json={'title': 'Manual test'}).json()['id']
    path = f'/api/conversations/{identity}/listing'
    listing = {'title': 'Old title', 'description': 'Old description', 'brand': 'Hanes', 'price': 25,
               **{f'{m}_specifics': {'title': 'Old title'} for m in MARKETPLACES}}
    first = client.put(path, headers=auth, json={'expected_revision_id': '', 'listing': listing})
    assert first.status_code == 200
    listing = first.json()['listing']
    listing['title'] = 'New title'
    second = client.put(path, headers=auth, json={'expected_revision_id': first.json()['revision_id'], 'listing': listing})
    assert second.status_code == 200
    saved = second.json()['listing']
    for market in MARKETPLACES:
        assert saved.get(f'{market}_specifics', {}).get('title', saved['title']) == 'New title'
    saved['poshmark_specifics'] = {**saved.get('poshmark_specifics', {}), 'title': 'My Poshmark title'}
    third = client.put(path, headers=auth, json={'expected_revision_id': second.json()['revision_id'], 'listing': saved})
    assert third.status_code == 200
    saved = third.json()['listing']
    saved['title'] = 'Another general title'
    fourth = client.put(path, headers=auth, json={'expected_revision_id': third.json()['revision_id'], 'listing': saved})
    assert fourth.status_code == 200
    assert fourth.json()['listing']['poshmark_specifics']['title'] == 'My Poshmark title'


@pytest.mark.parametrize(('quantity', 'expected'), [(1, 'sold'), (3, 'active')])
def test_paid_sales_reconcile_hosted_inventory_status(cloud, signing_keys, quantity, expected):  # noqa: F811
    from datetime import datetime

    from vendoo_studio.models.conversation import Conversation
    from vendoo_studio.models.marketplace_sale import MarketplaceSale

    client, engine = cloud
    alice = headers(signing_keys)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Recent eBay sale',
        'notes': '{"vendooStatus":"active"}'}).json()['id']
    with Session(engine) as db:
        db.get(Conversation, identity).status = 'active'
        db.add(ListingRevision(conversation_id=identity, user_id=USER_ID, source='agent',
            inventory_quantity=quantity, listing_json={'title': 'Recent eBay sale', 'quantity': quantity}))
        db.add(MarketplaceSale(user_id=USER_ID, conversation_id=identity, marketplace='ebay',
            order_id='recent-order', line_id='recent-line', quantity=1, sold_at=datetime.now()))
        db.commit()
    summary = client.get('/api/conversations', headers=alice).json()[0]
    assert summary['status'] == expected
    assert bool(summary['settled_at']) == (expected == 'sold')
    assert client.get(f'/api/conversations/{identity}', headers=alice).json()['status'] == expected
    assert client.get('/api/conversations', headers=headers(signing_keys, BOB)).json() == []
    with Session(engine) as db:
        assert db.get(Conversation, identity).status == expected


def test_missing_sale_badge_matches_ledger_and_clears_after_correction(cloud, signing_keys):  # noqa: F811
    from datetime import datetime

    from vendoo_studio.models.conversation import Conversation
    from vendoo_studio.models.marketplace_sale import MarketplaceSale

    client, engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Sold coat'}).json()['id']
    with Session(engine) as db:
        db.get(Conversation, identity).status = 'sold'
        sale = MarketplaceSale(user_id=USER_ID, conversation_id=identity, marketplace='ebay',
            order_id='badge-order', line_id='coat', sold_at=datetime.now(), quantity=1,
            financials={'currency': 'USD', 'item_revenue': '40', 'shipping_credit': '0',
                        'refunds': '0', 'shipping_cost': '4', 'item_cost': '5'})
        db.add(sale)
        db.commit()
        sale_id = sale.id
    summary = client.get('/api/conversations', headers=alice).json()[0]
    assert summary['status'] == 'sold'
    assert summary['missing_sale_data'] == ['fees']
    assert client.get(f'/api/conversations/{identity}', headers=alice).json()['missing_sale_data'] == ['fees']
    assert client.get('/api/conversations', headers=bob).json() == []
    assert client.get(f'/api/conversations/{identity}', headers=bob).status_code == 404
    with Session(engine) as db:
        sale = db.get(MarketplaceSale, sale_id)
        sale.financials = {**sale.financials, 'fees': '8'}
        db.commit()
    assert client.get('/api/conversations', headers=alice).json()[0]['missing_sale_data'] == []
    assert client.get(f'/api/conversations/{identity}', headers=alice).json()['missing_sale_data'] == []


def test_generated_category_provenance_does_not_block_editor_refresh(cloud, signing_keys):  # noqa: F811
    client, engine = cloud
    alice = headers(signing_keys)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'New draft'}).json()['id']
    with Session(engine) as db:
        revision = ListingRevision(conversation_id=identity, user_id=USER_ID, source='agent', listing_json={
            'title': 'Generated coat',
            'direct_category_ids': {'ebay': '123', 'ebay_path': 'Clothing > Coats', 'query': 'coat search provenance'},
        })
        db.add(revision)
        db.flush()
        revision_id = revision.id
        db.add(Listing(conversation_id=identity, user_id=USER_ID, current_revision_id=revision_id))
        db.commit()
    path = '/api/conversations/' + identity
    for suffix in ['/listing', '/revisions/' + revision_id]:
        response = client.get(path + suffix, headers=alice)
        assert response.status_code == 200, response.text
        fields = response.json()['listing']
        assert fields['title'] == 'Generated coat'
        assert fields['direct_category_ids'] == {'ebay': '123', 'ebay_path': 'Clothing > Coats'}
    rejected = client.put(path + '/listing', headers=alice, json={
        'listing': {'direct_category_ids': {'query': 'client-supplied provenance'}},
        'expected_revision_id': revision_id,
    })
    assert rejected.status_code == 422


def test_poshmark_category_choice_saves_and_reloads_its_native_form(cloud, signing_keys):  # noqa: F811
    client, _engine = cloud
    alice = headers(signing_keys)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Coat'}).json()['id']
    path = '/api/conversations/' + identity
    initial = client.put(path + '/listing', headers=alice, json={'listing': {'title': 'Coat'}, 'expected_revision_id': ''})
    assert initial.status_code == 200, initial.text
    form_path = path + '/native-marketplaces/poshmark/form'
    response = client.get(form_path, headers=alice)
    assert response.status_code == 200, response.text
    category = next(row for row in with_choice_lists(client, response.json(), alice)['fields']
                    if row['path'] == 'direct_category_ids.poshmark')
    picked = next(iter(category['option_values'].values()))
    payload = {'title': 'Coat', 'direct_category_ids': {'poshmark': picked}}
    saved = client.put(path + '/listing', headers=alice, json={'listing': payload, 'expected_revision_id': initial.json()['revision_id']})
    assert saved.status_code == 200, saved.text
    assert client.get(path + '/listing', headers=alice).json()['listing']['direct_category_ids']['poshmark'] == picked
    refreshed = client.get(form_path, headers=alice)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()['category_id'] == picked


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
    assert client.get('/api/settings/ui', headers=bob).json()['theme'] == 'system'
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
            # Storage deletes a photo together with its kept thumbnails.
            for name in hosted_photos.with_renditions(key):
                stored.pop(name, None)
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


def test_cover_thumbnails_are_made_once(cloud, signing_keys, monkeypatch):  # noqa: F811
    # Scrolling the inventory asks for the same covers again and again. Each
    # ask must not download and resize the full photo from Storage again.
    client, _engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Coat'}).json()['id']
    stored, reads = {}, []

    def storage(method, key, _token, data=None):
        if method == 'POST':
            stored[key] = data
        if method == 'GET' and not key.endswith(('.t96.jpg', '.t192.jpg')):
            reads.append(key)
        return stored.get(key, b'')
    monkeypatch.setattr(hosted_photos, 'storage_call', storage)
    image = io.BytesIO()
    Image.new('RGB', (2400, 1200), 'red').save(image, 'JPEG')
    photo = client.post('/api/drafts/' + identity + '/photos', headers=alice, content=image.getvalue()).json()['id']
    cover_url = client.get('/api/conversations', headers=alice).json()[0]['cover_photo_url']

    first = client.get(cover_url, headers=alice)
    again = client.get(cover_url, headers=alice)

    assert first.status_code == again.status_code == 200
    assert first.content == again.content
    with Image.open(io.BytesIO(first.content)) as result:
        assert result.size == (96, 48)
    assert len(reads) == 1
    # The cache never answers for a seller who does not own the photo.
    assert client.get(cover_url, headers=bob).status_code == 404
    assert client.get('/api/photos/' + photo + '/thumb?size=192', headers=alice).status_code == 200
    assert len(reads) == 2



def test_cover_thumbnails_survive_a_new_server_process(cloud, signing_keys, monkeypatch):  # noqa: F811
    # Every deploy starts a new process with an empty memory cache. A cover made
    # before must come from Storage, not be remade from the full photo.
    client, _engine = cloud
    alice = headers(signing_keys)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Coat'}).json()['id']
    stored, reads, deletes = {}, [], []

    def storage(method, key, _token, data=None):
        if method == 'POST':
            stored[key] = data
        if method == 'GET':
            reads.append(key)
        if method == 'DELETE':
            # Storage deletes a photo together with its kept thumbnails.
            deletes.extend(hosted_photos.with_renditions(key))
            for name in hosted_photos.with_renditions(key):
                stored.pop(name, None)
        return stored.get(key, b'')
    monkeypatch.setattr(hosted_photos, 'storage_call', storage)
    image = io.BytesIO()
    Image.new('RGB', (2400, 1200), 'red').save(image, 'JPEG')
    photo = client.post('/api/drafts/' + identity + '/photos', headers=alice, content=image.getvalue()).json()['id']
    original = next(key for key in stored if not key.endswith('.t96.jpg'))
    cover_url = client.get('/api/conversations', headers=alice).json()[0]['cover_photo_url']

    first = client.get(cover_url, headers=alice)
    assert hosted_photos.rendition_key(original, 96) in stored
    hosted_photos._thumbs.clear()
    reads.clear()
    after_deploy = client.get(cover_url, headers=alice)

    assert after_deploy.status_code == 200
    assert after_deploy.content == first.content
    assert reads == [hosted_photos.rendition_key(original, 96)]
    # Deleting the photo deletes its kept thumbnails with it.
    assert client.delete('/api/drafts/' + identity + '/photos/' + photo, headers=alice).status_code == 204
    assert hosted_photos.rendition_key(original, 96) in deletes
    assert not stored


def test_phone_photo_tiles_are_kept_like_covers(cloud, signing_keys, monkeypatch):  # noqa: F811
    # The iPhone editor's tiles are 384 px. Remaking each from the full photo
    # after every deploy left the seller watching tiles fill in one at a time.
    client, _engine = cloud
    alice = headers(signing_keys)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Coat'}).json()['id']
    stored, reads = {}, []

    def storage(method, key, _token, data=None):
        if method == 'POST':
            stored[key] = data
        if method == 'GET':
            reads.append(key)
        return stored.get(key, b'')
    monkeypatch.setattr(hosted_photos, 'storage_call', storage)
    image = io.BytesIO()
    Image.new('RGB', (2400, 1200), 'red').save(image, 'JPEG')
    photo = client.post('/api/drafts/' + identity + '/photos', headers=alice, content=image.getvalue()).json()['id']
    original = next(key for key in stored if '.t' not in key)

    first = client.get('/api/photos/' + photo + '/thumb?size=384', headers=alice)
    with Image.open(io.BytesIO(first.content)) as result:
        assert result.size == (384, 192)
    hosted_photos._thumbs.clear()
    reads.clear()
    after_deploy = client.get('/api/photos/' + photo + '/thumb?size=384', headers=alice)
    assert after_deploy.content == first.content
    assert reads == [hosted_photos.rendition_key(original, 384)]


def test_job_list_skips_the_listing_snapshot(cloud, signing_keys):  # noqa: F811
    # Each job keeps a full listing copy (tens of kilobytes). The list shows
    # status only, so it should not read those copies on every request.
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    from vendoo_studio.models.job import Job

    client, engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Coat'}).json()['id']
    with Session(engine) as db:
        db.add(Job(id='job-1', conversation_id=identity, approved_revision_id='rev', user_id=USER_ID,
            listing_snapshot={'title': 'Coat', 'description': 'x' * 50000}, status='completed'))
        db.commit()
    statements = []
    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(Engine, 'before_cursor_execute', record)
    try:
        listed = client.get('/api/jobs', headers=alice)
        scoped = client.get('/api/jobs', headers=alice, params={'conversation_id': identity})
    finally:
        event.remove(Engine, 'before_cursor_execute', record)
    assert listed.status_code == 200, listed.text
    assert [row['id'] for row in listed.json()] == ['job-1']
    assert scoped.json() == listed.json()
    assert 'listing_snapshot' not in listed.json()[0]
    job_reads = [statement for statement in statements if 'FROM jobs' in statement]
    assert job_reads
    assert not any('listing_snapshot' in statement for statement in job_reads), job_reads
    assert client.get('/api/jobs', headers=bob).json() == []


def test_screen_matches_core_reads_and_refuses_other_sellers(cloud, signing_keys):  # noqa: F811
    client, _engine = cloud
    alice = headers(signing_keys)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Coat'}).json()['id']
    path = '/api/conversations/' + identity
    from sqlalchemy import event
    statements = []

    def record(_connection, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(_engine, 'before_cursor_execute', record)
    try:
        response = client.get(path + '/screen', headers=alice)
    finally:
        event.remove(_engine, 'before_cursor_execute', record)
    # One ownership read; activity reuses the messages already loaded for chat.
    assert sum('FROM conversations' in sql for sql in statements) == 1
    assert sum('FROM messages' in sql for sql in statements) == 1
    assert response.status_code == 200, response.text
    for key, suffix in [('conversation', ''), ('listing', '/listing'), ('photos', '/photos'),
                        ('messages', '/messages'), ('activity', '/activity')]:
        assert response.json()[key] == client.get(path + suffix, headers=alice).json()
    assert response.json()['jobs'] == client.get('/api/jobs', params={'conversation_id': identity}, headers=alice).json()
    assert client.get(path + '/screen', headers=headers(signing_keys, BOB)).status_code == 404
    assert client.get(path + '/screen').status_code == 401


def test_thumbnails_are_kept_by_the_browser_and_read_one_row(cloud, signing_keys, monkeypatch):  # noqa: F811
    """A photo's thumbnail URL changes with the photo, so the browser keeps it. Each
    query is a network round trip on the hosted database, so a thumbnail reads one row."""
    from sqlalchemy import event

    client, engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Coat'}).json()['id']
    stored = {}

    def storage(method, key, token, data=None):
        if method == 'POST':
            stored[key] = data
        return stored.get(key, b'')
    monkeypatch.setattr(hosted_photos, 'storage_call', storage)
    image = io.BytesIO()
    Image.new('RGB', (200, 100), 'red').save(image, 'JPEG')
    assert client.post('/api/drafts/' + identity + '/photos', headers=alice, content=image.getvalue()).status_code == 201
    cover = client.get('/api/conversations', headers=alice).json()[0]['cover_photo_url']
    photo = cover.split('/')[3]
    queries = []
    listen = lambda *args: queries.append(args[2])  # noqa: E731
    event.listen(engine, 'before_cursor_execute', listen)
    try:
        kept = client.get(cover, headers=alice)
    finally:
        event.remove(engine, 'before_cursor_execute', listen)
    assert kept.status_code == 200
    assert 'immutable' in kept.headers['cache-control'] and 'private' in kept.headers['cache-control']
    assert len([query for query in queries if 'photos' in query]) == 1, queries
    # Without the photo's version the URL could outlive the photo.
    unversioned = client.get('/api/photos/' + photo + '/thumb?size=96', headers=alice)
    assert unversioned.status_code == 200 and 'immutable' not in unversioned.headers.get('cache-control', '')
    assert client.get(cover, headers=bob).status_code == 404


def test_activity_names_a_running_marketplace_action_for_the_seller(cloud, signing_keys):  # noqa: F811
    from vendoo_studio.models.job import Job

    client, engine = cloud
    alice = headers(signing_keys)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Tee'}).json()['id']
    with Session(engine) as db:
        db.add(Job(id='job-1', conversation_id=identity, approved_revision_id='rev', user_id=USER_ID,
                   listing_snapshot={}, status='dispatched', current_step='native_poshmark_list'))
        db.commit()
    activity = client.get(f'/api/conversations/{identity}/activity', headers=alice).json()
    assert activity['busy'] is True
    # Listing on one marketplace never locks List on the others.
    assert activity['writing'] is False
    assert activity['items'] == ['Poshmark: Listing…']


def test_general_category_fills_each_marketplace_category_on_a_hosted_save(cloud, signing_keys, monkeypatch):  # noqa: F811
    """The iPhone and web app save through this route: a new General category fills each form's own category."""
    from unittest.mock import AsyncMock

    from vendoo_studio.services import direct_categories

    # The save never waits on a marketplace: it matches against the lists Anteroom already holds.
    monkeypatch.setattr(direct_categories, '_ebay_leaf', AsyncMock(side_effect=AssertionError('eBay call')))
    monkeypatch.setattr(direct_categories, '_etsy_leaf', AsyncMock(side_effect=AssertionError('Etsy call')))
    client, _engine = cloud
    alice = headers(signing_keys)
    identity = client.post('/api/conversations', headers=alice, json={'title': 'Tee'}).json()['id']
    path = f'/api/conversations/{identity}/listing'
    first = client.put(path, headers=alice, json={'expected_revision_id': '', 'listing': {'title': 'Nike tee'}})
    assert first.status_code == 200, first.text
    tee = "Clothing, Shoes & Accessories > Men > Men's Clothing > Shirts > T-Shirts"
    saved = client.put(path, headers=alice, json={'expected_revision_id': first.json()['revision_id'],
                                                  'listing': {**first.json()['listing'], 'category_path': tee}})
    assert saved.status_code == 200, saved.text
    categories = saved.json()['listing'].get('direct_category_ids') or {}
    assert categories.get('depop') == 'menswear_tshirts'
    assert categories.get('mercari')
    assert categories.get('poshmark')


def test_hosted_inventory_reports_unsent_edits(cloud, signing_keys):  # noqa: F811
    from vendoo_studio.models.marketplace_listing import MarketplaceListing

    client, engine = cloud
    auth = headers(signing_keys)
    identity = client.post('/api/conversations', headers=auth, json={'title': 'Edited item'}).json()['id']
    path = f'/api/conversations/{identity}/listing'
    first = client.put(path, headers=auth, json={'expected_revision_id': '', 'listing': {'title': 'Original', 'price': 25}}).json()
    with Session(engine) as db:
        db.add(MarketplaceListing(user_id=USER_ID, conversation_id=identity, marketplace='ebay',
                                  state='published', revision_id=first['revision_id']))
        db.commit()
    def row():
        return next(row for row in client.get('/api/conversations', headers=auth).json() if row['id'] == identity)
    assert row()['unsent_edits'] is False
    saved = client.put(path, headers=auth, json={'expected_revision_id': first['revision_id'],
        'listing': {**first['listing'], 'price': 30}})
    assert saved.status_code == 200
    assert row()['unsent_edits'] is True
    assert client.get('/api/conversations', headers=headers(signing_keys, BOB)).json() == []


def test_discard_blank_removes_only_an_untouched_new_listing(cloud, signing_keys):  # noqa: F811
    client, _ = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    blank = client.post('/api/conversations', headers=alice, json={'title': 'New Listing'}).json()['id']
    typed = client.post('/api/conversations', headers=alice, json={'title': 'New Listing'}).json()['id']
    assert client.patch('/api/conversations/' + typed, headers=alice, json={'notes': 'Stain on cuff'}).status_code == 200
    assert client.post(f'/api/conversations/{blank}/discard-blank', headers=bob).status_code == 404
    assert client.post(f'/api/conversations/{blank}/discard-blank', headers=alice).json() == {'discarded': True}
    assert client.get('/api/conversations/' + blank, headers=alice).status_code == 404
    assert client.post(f'/api/conversations/{typed}/discard-blank', headers=alice).json() == {'discarded': False}
    assert client.get('/api/conversations/' + typed, headers=alice).status_code == 200
