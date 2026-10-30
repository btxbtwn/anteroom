import io
import json

import httpx
import pytest
from PIL import Image
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.models.conversation import Photo
from vendoo_studio.models.listing import Listing, ListingRevision
from vendoo_studio.services import hosted_photos, schema_migrations


def image_bytes():
    output = io.BytesIO()
    image = Image.new('RGB', (40, 30), 'red')
    exif = image.getexif()
    exif[270] = 'Private camera metadata'
    image.save(output, format='JPEG', exif=exif)
    return output.getvalue()


def test_edit_general_fields_as_a_private_revision(cloud, signing_keys):  # noqa: F811
    client, engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    draft = client.post('/api/drafts', headers=alice, json={'title': 'Coat 10%'}).json()
    path = '/api/drafts/' + draft['id']
    fields = {'description': 'Wool coat', 'price': 42.5, 'quantity': 2, 'brand': 'Pendleton', 'sku': 'COAT-1'}
    body = {'title': 'Coat 10%', 'notes': 'Measurements checked', 'fields': fields, 'expected_updated_at': draft['updated_at']}
    for rejected in ({'user_id': BOB}, {'current_revision_id': 'foreign'}, {'ebay_specifics': {}}, {'quantity': 0}, {'quantity': 1.5}, {'price': -1}):
        assert client.patch(path, headers=alice, json={**body, 'fields': {**fields, **rejected}}).status_code == 422
    assert client.patch(path, headers=bob, json=body).status_code == 404
    saved = client.patch(path, headers=alice, json=body)
    assert saved.status_code == 200
    assert saved.json()['fields']['price'] == 42.5
    assert client.get(path, headers=alice).json() == saved.json()
    assert client.patch(path, headers=alice, json=body).status_code == 409
    assert client.get('/api/drafts?search=10%25', headers=alice).json()[0]['id'] == draft['id']
    assert client.get('/api/drafts?search=10_', headers=alice).json() == []
    assert client.get('/api/drafts?offset=1', headers=alice).json() == []
    with Session(engine) as db:
        revision = db.get(ListingRevision, db.get(Listing, draft['id']).current_revision_id)
        assert revision.user_id == USER_ID
        assert revision.inventory_quantity == 2
        assert revision.listing_json['title'] == 'Coat 10%'


def test_photo_lifecycle_checks_owner_and_never_uses_local_files(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    draft = client.post('/api/drafts', headers=alice, json={'title': 'Coat'}).json()
    path = '/api/drafts/' + draft['id'] + '/photos'
    stored = {}
    calls = []

    def storage(method, key, token, data=None):
        assert key.startswith(USER_ID + '/' + draft['id'] + '/')
        assert token == alice['Authorization'].partition(' ')[2]
        calls.append(method)
        if method == 'POST':
            stored[key] = data
        if method == 'DELETE':
            stored.pop(key, None)
        return stored.get(key, b'')

    monkeypatch.setattr(hosted_photos, 'storage_call', storage)
    assert client.post(path, headers=bob, content=image_bytes()).status_code == 404
    assert calls == []
    assert client.post(path, headers=alice, content=b'not an image').status_code == 422
    assert client.post(path, headers=alice, content=b'x' * (hosted_photos.MAX_BYTES + 1)).status_code == 413
    uploaded = client.post(path, headers=alice, content=image_bytes())
    assert uploaded.status_code == 201
    photo = uploaded.json()
    assert set(photo) == {'id', 'width', 'height', 'size_bytes'}
    assert photo['width'] == 40 and photo['height'] == 30
    assert client.get(path, headers=alice).json() == [photo]
    photo_path = path + '/' + photo['id']
    assert client.get(photo_path).status_code == 401
    assert client.get(path, headers=bob).status_code == 404
    assert client.get(photo_path, headers=bob).status_code == 404
    assert client.delete(photo_path, headers=bob).status_code == 404
    downloaded = client.get(photo_path, headers=alice)
    assert downloaded.headers['cache-control'] == 'no-store'
    with Image.open(io.BytesIO(downloaded.content)) as image:
        assert not image.getexif()
    with Session(engine) as db:
        row = db.scalar(select(Photo).where(Photo.id == photo['id']))
        assert row.user_id == USER_ID
        assert row.stored_filename in stored
    assert client.delete(photo_path, headers=alice).status_code == 204
    assert stored == {}
    assert client.get(photo_path, headers=alice).status_code == 404
    assert client.get(path, headers=alice).json() == []


def test_storage_requests_use_seller_credentials_and_sdk_delete_contract(monkeypatch):
    monkeypatch.setenv('SUPABASE_URL', 'https://example.supabase.co')
    monkeypatch.setenv('SUPABASE_PUBLISHABLE_KEY', 'public-test-key')
    captured = []

    def request(method, url, **kwargs):
        captured.append((method, url, kwargs))
        return httpx.Response(200, content=b'photo')

    monkeypatch.setattr(httpx, 'request', request)
    key = USER_ID + '/draft/' + 'a' * 32 + '.jpg'
    assert hosted_photos.storage_call('GET', key, 'seller-token') == b'photo'
    hosted_photos.storage_call('DELETE', key, 'seller-token')
    assert captured[0][2]['headers']['Authorization'] == 'Bearer seller-token'
    assert captured[1][1].endswith('/object/anteroom-photos')
    assert captured[1][2]['json'] == {'prefixes': [key]}
    monkeypatch.setattr(httpx, 'request', lambda *args, **kwargs: httpx.Response(500, text='PRIVATE diagnostic'))
    with pytest.raises(Exception) as error:
        hosted_photos.storage_call('POST', key, 'seller-token', b'data')
    assert 'PRIVATE' not in str(error.value)


def test_storage_policies_enforce_owner_even_with_a_broad_policy(migration_engine):  # noqa: F811
    engine = migration_engine
    if engine.dialect.name != 'postgresql':
        pytest.skip('Supabase Storage policies require Postgres')
    # Model the Storage-owned schema locally before applying the real revision.
    with engine.begin() as connection:
        for role in ('authenticated', 'anon'):
            connection.execute(text(f"DO $$ BEGIN CREATE ROLE {role}; EXCEPTION WHEN duplicate_object THEN NULL; END $$"))
        connection.execute(text('CREATE SCHEMA storage; CREATE SCHEMA auth'))
        connection.execute(text('''CREATE TABLE storage.buckets (id text PRIMARY KEY, name text, public boolean,
            file_size_limit bigint, allowed_mime_types text[]);
            CREATE TABLE storage.objects (bucket_id text, name text);
            ALTER TABLE storage.objects ENABLE ROW LEVEL SECURITY;
            GRANT USAGE ON SCHEMA storage, auth TO authenticated, anon;
            GRANT SELECT, INSERT, DELETE ON storage.objects TO authenticated, anon;
            CREATE FUNCTION auth.jwt() RETURNS jsonb LANGUAGE sql STABLE AS
                $$ SELECT nullif(current_setting('request.jwt.claims', true), '')::jsonb $$;
            CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$ SELECT (auth.jwt()->>'sub')::uuid $$;
            CREATE FUNCTION storage.foldername(text) RETURNS text[] LANGUAGE sql IMMUTABLE AS $$ SELECT string_to_array($1, '/') $$;
            CREATE POLICY broad_access ON storage.objects FOR ALL TO PUBLIC USING (true) WITH CHECK (true);
        '''))
    schema_migrations.ensure_schema(engine)
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT public FROM storage.buckets WHERE id='anteroom-photos'")) is False
        connection.execute(text('INSERT INTO storage.objects VALUES (:bucket, :name)'), {'bucket': 'anteroom-photos', 'name': USER_ID + '/draft/photo.jpg'})
    for role, user, anonymous, expected in [('authenticated', BOB, False, 0), ('anon', USER_ID, True, 0), ('authenticated', USER_ID, False, 1)]:
        with engine.begin() as connection:
            connection.execute(text(f'SET LOCAL ROLE {role}'))
            connection.execute(text("SELECT set_config('request.jwt.claims', :claims, true)"),
                               {'claims': json.dumps({'sub': user, 'is_anonymous': anonymous})})
            if expected == 0:
                with pytest.raises(DBAPIError), connection.begin_nested():
                    connection.execute(text("INSERT INTO storage.objects VALUES ('anteroom-photos', :name)"),
                                       {'name': USER_ID + '/draft/forged.jpg'})
            else:
                savepoint = connection.begin_nested()
                connection.execute(text("INSERT INTO storage.objects VALUES ('anteroom-photos', :name)"),
                                   {'name': USER_ID + '/draft/own.jpg'})
                savepoint.rollback()
            assert len(connection.execute(text("SELECT * FROM storage.objects WHERE bucket_id='anteroom-photos'")).all()) == expected
            assert connection.execute(text("DELETE FROM storage.objects WHERE name LIKE :prefix"), {'prefix': USER_ID + '/%'}).rowcount == expected


def test_revision_pointer_cannot_expose_another_draft(cloud, signing_keys):  # noqa: F811
    client, engine = cloud
    alice = headers(signing_keys)
    first = client.post('/api/drafts', headers=alice, json={'title': 'First'}).json()
    second = client.post('/api/drafts', headers=alice, json={'title': 'Second'}).json()
    with Session(engine) as db:
        secret = ListingRevision(user_id=USER_ID, conversation_id=second['id'], source='manual',
                                 listing_json={'description': 'Other draft private data', 'api_key': 'must never return'})
        db.add(secret)
        db.flush()
        db.add(Listing(user_id=USER_ID, conversation_id=first['id'], current_revision_id=secret.id))
        db.commit()
    response = client.get('/api/drafts/' + first['id'], headers=alice)
    assert response.status_code == 200
    assert response.json()['fields']['description'] == ''
    assert 'must never return' not in response.text


def test_failed_photo_commit_cleans_up_storage_without_leaking_diagnostics(cloud, signing_keys, monkeypatch):  # noqa: F811
    from sqlalchemy.exc import SQLAlchemyError
    from vendoo_studio.services.hosted_database import SellerDatabaseSession

    client, _engine = cloud
    alice = headers(signing_keys)
    draft = client.post('/api/drafts', headers=alice, json={'title': 'Coat'}).json()
    calls = []
    monkeypatch.setattr(hosted_photos, 'storage_call', lambda method, *args: calls.append(method) or b'')

    def fail_commit(db):
        raise SQLAlchemyError('PRIVATE database diagnostic')

    monkeypatch.setattr(SellerDatabaseSession, 'commit', fail_commit)
    response = client.post('/api/drafts/' + draft['id'] + '/photos', headers=alice, content=image_bytes())
    assert response.status_code == 503
    assert 'PRIVATE' not in response.text
    assert calls == ['POST', 'DELETE']
    assert client.get('/api/drafts/' + draft['id'] + '/photos', headers=alice).json() == []
