"""Account deletion: only the signed-in seller's data, sign-in, photos and credentials go."""
import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_hosted_service_storage import storage, vault_storage  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.models.background_task import BackgroundTask
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.job import Job
from vendoo_studio.models.marketplace_listing import MarketplaceListing
from vendoo_studio.models.user_preference import UserPreference
from vendoo_studio.services import account_deletion
from vendoo_studio.services.hosted_context import VaultSecretStore

ALICE = USER_ID


def supabase_schemas(engine):
    """Stand-ins for the Supabase tables the private functions reach."""
    with engine.begin() as db:
        db.execute(text("""
            CREATE SCHEMA auth; CREATE TABLE auth.users (id uuid PRIMARY KEY);
            CREATE SCHEMA storage; CREATE TABLE storage.objects (bucket_id text, name text);
        """))
        db.execute(text('INSERT INTO auth.users VALUES (:a), (:b)'), {'a': ALICE, 'b': BOB})
        for owner in (ALICE, BOB):
            db.execute(text("INSERT INTO storage.objects VALUES ('anteroom-photos', :photo), "
                            "('anteroom-photos', :chat), ('other-bucket', :other)"),
                       {'photo': f'{owner}/item/{"a" * 32}.jpg', 'chat': f'{owner}/thread/{"b" * 32}.jpg',
                        'other': f'{owner}/x.jpg'})
            db.execute(text("INSERT INTO anteroom_private.device_routes (user_id, device, machine_id, base_url) "
                            "VALUES (:owner, 'mac', 'machine', 'http://internal')"), {'owner': owner})


def seed(engine, owner, *, job_status='completed'):
    with Session(engine) as db:
        item = Conversation(user_id=owner, title=f'Coat of {owner}')
        db.add(item)
        db.flush()
        db.add_all([
            Job(user_id=owner, conversation_id=item.id, approved_revision_id='r', listing_snapshot={}, status=job_status),
            MarketplaceListing(user_id=owner, conversation_id=item.id, marketplace='ebay', state='published',
                               listing_id='live-1'),
            BackgroundTask(user_id=owner, conversation_id=item.id, kind='chat', payload={}, status='running'),
            UserPreference(user_id=owner, key='settings', value={'theme': 'dark'}),
        ])
        db.commit()
        return item.id


def owned_rows(engine, owner):
    """Rows the owner still has in every seller table, read as the table owner."""
    counts = {}
    with engine.connect() as db:
        for name in [table.name for table in account_deletion.seller_tables()] + sorted(account_deletion.DELETED_BY_DATABASE):
            counts[name] = db.scalar(text(f'SELECT count(*) FROM "{name}" WHERE user_id = :owner'), {'owner': owner})
    return {name: count for name, count in counts.items() if count}


def test_every_seller_table_in_the_database_is_deleted(cloud):  # noqa: F811
    _client, engine = cloud
    covered = {table.name for table in account_deletion.seller_tables()} | account_deletion.DELETED_BY_DATABASE
    with engine.connect() as db:
        public = set(db.scalars(text("""
            SELECT DISTINCT relation.relname FROM pg_class AS relation
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            JOIN pg_attribute AS attribute ON attribute.attrelid = relation.oid
            WHERE namespace.nspname = 'public' AND relation.relkind = 'r'
              AND attribute.attname = 'user_id' AND NOT attribute.attisdropped""")))
        policed = set(db.scalars(text("""
            SELECT relation.relname FROM pg_class AS relation JOIN pg_policy AS policy ON policy.polrelid = relation.oid
            WHERE policy.polname = 'seller_owner'""")))
        private = set(db.scalars(text("""
            SELECT DISTINCT relation.relname FROM pg_class AS relation
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            JOIN pg_attribute AS attribute ON attribute.attrelid = relation.oid
            WHERE namespace.nspname = 'anteroom_private' AND relation.relkind = 'r' AND attribute.attname = 'user_id'""")))
    # A new seller table must be added to the models (and so to the deletion) or this fails.
    assert public == policed == covered
    # anteroom_private.delete_account() clears each private seller table by name.
    assert private == {'device_routes'}


def test_seller_deletes_only_their_own_account(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    supabase_schemas(engine)
    removed = []
    monkeypatch.setattr('vendoo_studio.services.hosted_photos.remove_objects',
                        lambda keys, token: removed.append((list(keys), token)))
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    seed(engine, ALICE)
    bob_item = seed(engine, BOB)
    bob_before = owned_rows(engine, BOB)

    assert client.request('DELETE', '/api/account', json={'confirm': True}).status_code == 401
    for body in ({}, {'confirm': False}, {'confirm': 'yes'}, {'confirm': True, 'user_id': BOB}):
        assert client.request('DELETE', '/api/account', headers=alice, json=body).status_code == 422
    assert 'account_deletion' in client.get('/api/runtime', headers=alice).json()['features']

    response = client.request('DELETE', '/api/account', headers=alice, json={'confirm': True})
    assert response.status_code == 200, response.text
    assert response.json() == {'deleted': True}
    assert owned_rows(engine, ALICE) == {}
    # Only Alice's photo and chat images, sent with her own session.
    assert removed == [([f'{ALICE}/item/{"a" * 32}.jpg', f'{ALICE}/thread/{"b" * 32}.jpg'],
                        alice['Authorization'].removeprefix('Bearer '))]
    with engine.connect() as db:
        assert set(map(str, db.scalars(text('SELECT id FROM auth.users')))) == {BOB}
        assert set(map(str, db.scalars(text('SELECT user_id FROM anteroom_private.device_routes')))) == {BOB}
    assert owned_rows(engine, BOB) == bob_before
    assert client.get('/api/conversations/' + bob_item, headers=bob).status_code == 200
    bob_before = owned_rows(engine, BOB)

    # Safe to retry: a second request, with the same still-valid token, finds nothing left.
    again = client.request('DELETE', '/api/account', headers=alice, json={'confirm': True})
    assert again.status_code == 200, again.text
    assert owned_rows(engine, BOB) == bob_before


def test_running_marketplace_work_refuses_and_deletes_nothing(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    supabase_schemas(engine)
    monkeypatch.setattr('vendoo_studio.services.hosted_photos.remove_objects',
                        lambda *_: pytest.fail('Nothing may be deleted while a marketplace action runs'))
    seed(engine, ALICE, job_status='dispatched')
    before = owned_rows(engine, ALICE)
    response = client.request('DELETE', '/api/account', headers=headers(signing_keys), json={'confirm': True})
    assert response.status_code == 409
    assert 'Nothing was deleted' in response.json()['detail']
    assert owned_rows(engine, ALICE) == before
    with Session(engine) as db:
        db.execute(text("UPDATE jobs SET status = 'completed' WHERE user_id = :owner"), {'owner': ALICE})
        db.execute(text("UPDATE marketplace_listings SET state = 'unknown' WHERE user_id = :owner"), {'owner': ALICE})
        db.commit()
    assert client.request('DELETE', '/api/account', headers=headers(signing_keys), json={'confirm': True}).status_code == 409
    assert owned_rows(engine, ALICE) == before


def test_unready_database_refuses_before_deleting(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    monkeypatch.setattr('vendoo_studio.services.hosted_photos.remove_objects',
                        lambda *_: pytest.fail('Nothing may be deleted when the auth user cannot be'))
    seed(engine, ALICE)
    with engine.begin() as db:
        db.execute(text('CREATE OR REPLACE FUNCTION anteroom_private.account_deletion_ready() RETURNS boolean '
                        'LANGUAGE sql AS $$ SELECT false $$'))
    before = owned_rows(engine, ALICE)
    response = client.request('DELETE', '/api/account', headers=headers(signing_keys), json={'confirm': True})
    assert response.status_code == 503
    assert owned_rows(engine, ALICE) == before


def test_private_functions_only_reach_the_claimed_seller(cloud):  # noqa: F811
    _client, engine = cloud
    supabase_schemas(engine)
    from vendoo_studio.services.hosted_database import SellerDatabaseSession
    with SellerDatabaseSession(user_id=BOB, bind=engine) as db:
        assert list(db.scalars(text('SELECT * FROM anteroom_private.account_photo_keys()'))) == [
            f'{BOB}/item/{"a" * 32}.jpg', f'{BOB}/thread/{"b" * 32}.jpg']
    # Without a verified seller claim the functions refuse.
    with engine.connect() as db:
        db.execute(text("SELECT set_config('role', 'anteroom_seller', true)"))
        for function in ('delete_account()', 'account_photo_keys()', 'account_deletion_ready()'):
            with pytest.raises(DBAPIError, match='A verified seller is required'), db.begin_nested():
                db.execute(text(f'SELECT anteroom_private.{function}')).all()
    with engine.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM auth.users')) == 2


def test_vault_credentials_of_only_the_deleted_seller_go(vault_storage):  # noqa: F811
    engine, sessions = vault_storage
    for owner in (ALICE, BOB):
        VaultSecretStore(sessions(owner), owner).set('marketplace-accounts', f'fixture-{owner}')
        VaultSecretStore(sessions(owner), owner).set('ai-api-keys', f'fixture-ai-{owner}')
    from vendoo_studio import database
    original = database.engine
    database.engine = engine
    try:
        account_deletion._delete_records(ALICE)
    finally:
        database.engine = original
    with engine.connect() as db:
        names = set(db.scalars(text('SELECT name FROM vault.secrets')))
        assert names == {f'anteroom:{BOB}:marketplace-accounts', f'anteroom:{BOB}:ai-api-keys'}
        assert set(db.scalars(text('SELECT DISTINCT user_id FROM user_secret_references'))) == {BOB}
    assert VaultSecretStore(sessions(BOB), BOB).get('marketplace-accounts') == f'fixture-{BOB}'
    assert VaultSecretStore(sessions(ALICE), ALICE).get('marketplace-accounts') is None


def test_relay_device_is_forgotten_or_deletion_stops(monkeypatch):
    calls, reply = [], {'status': 200}

    class Reply:
        def __init__(self, status):
            self.status_code = status

    async def relay(method, path, **_kwargs):
        calls.append((method, path))
        return Reply(reply['status'])

    monkeypatch.setattr('vendoo_studio.services.keychain.get_relay_device', lambda: {'deviceId': 'd', 'token': 't'})
    monkeypatch.setattr('vendoo_studio.services.relay_client.relay_request', relay)
    asyncio.run(account_deletion._forget_relay_device())
    # 401: the relay already forgot this device, as after an earlier attempt.
    reply['status'] = 401
    asyncio.run(account_deletion._forget_relay_device())
    assert calls == [('DELETE', '/studio/device')] * 2
    reply['status'] = 500
    with pytest.raises(account_deletion.AccountDeletionRefused) as refusal:
        asyncio.run(account_deletion._forget_relay_device())
    assert refusal.value.status == 503
    monkeypatch.setattr('vendoo_studio.services.keychain.get_relay_device', lambda: None)
    asyncio.run(account_deletion._forget_relay_device())
    assert len(calls) == 3


def test_photos_are_removed_in_batches_with_the_seller_session(monkeypatch):
    from vendoo_studio.services import hosted_photos
    monkeypatch.setenv('SUPABASE_URL', 'https://project.supabase.co')
    monkeypatch.setenv('SUPABASE_PUBLISHABLE_KEY', 'public-key')
    sent = []

    class Reply:
        status_code, is_success = 200, True

    def request(method, url, **kwargs):
        sent.append((method, url, kwargs['headers']['Authorization'], len(kwargs['json']['prefixes'])))
        return Reply()

    monkeypatch.setattr(hosted_photos._storage, 'request', request)
    hosted_photos.remove_objects([f'{ALICE}/item/{uuid.uuid4().hex}.jpg' for _ in range(2500)], 'seller-jwt')
    assert sent == [('DELETE', 'https://project.supabase.co/storage/v1/object/anteroom-photos', 'Bearer seller-jwt', size)
                    for size in (1000, 1000, 500)]


def test_mac_app_has_no_account_deletion():
    from vendoo_studio.main import app
    assert not any(getattr(route, 'path', None) == '/api/account' for route in app.routes)
    assert TestClient(app).request('DELETE', '/api/account', json={'confirm': True}).status_code in {404, 405}
