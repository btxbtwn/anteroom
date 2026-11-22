"""Account deletion: only the signed-in seller's data, sign-in, photos and credentials go."""
import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_hosted_service_storage import storage, vault_storage  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from anteroom.models.background_task import BackgroundTask
from anteroom.models.conversation import Conversation
from anteroom.models.job import Job
from anteroom.models.marketplace_listing import MarketplaceListing
from anteroom.models.plaid import PlaidConnection, PlaidTransaction
from anteroom.models.user_preference import UserPreference
from anteroom.services import account_deletion
from anteroom.services.hosted_context import VaultSecretStore
from anteroom.services.plaid_client import PlaidClient, PlaidError

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
    assert private == {'device_routes', 'account_cleanup'}


def test_seller_deletes_only_their_own_account(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    supabase_schemas(engine)
    removed = []
    monkeypatch.setattr('anteroom.services.hosted_photos.remove_objects',
                        lambda keys, token: removed.append((list(keys), token)))
    monkeypatch.setattr(PlaidClient, 'configured', lambda: PlaidClient('sandbox', 'fixture', 'fixture'))
    monkeypatch.setattr(PlaidClient, 'post', lambda *_args, **_kwargs: pytest.fail('No Plaid Items to revoke'))
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    seed(engine, ALICE)
    bob_item = seed(engine, BOB)
    expense_path = '/api/business-expenses'
    expense = {'incurred_date': '2026-01-10', 'amount_cents': 100, 'category': 'other'}
    alice_expense = client.post(expense_path, headers=alice, json=expense)
    assert alice_expense.status_code == 201, alice_expense.text
    bob_import = client.post(f'{expense_path}/import', headers=bob, json={
        'content': 'incurred_date,amount_cents,category\n2026-01-10,200,other\n',
    })
    assert bob_import.status_code == 200, bob_import.text
    assert bob_import.json()['imported'] == 1
    assert client.put(f"{expense_path}/{alice_expense.json()['id']}", headers=bob,
                      json={**expense, 'expected_revision': alice_expense.json()['revision']}).status_code == 404
    bob_expenses = client.get(expense_path, headers=bob, params={'start': '2026-01-01', 'end': '2026-01-31'}).json()
    assert [row['amount_cents'] for row in bob_expenses] == [200]
    bob_before = owned_rows(engine, BOB)

    assert client.request('DELETE', '/api/account', json={'confirm': True}).status_code == 401
    for body in ({}, {'confirm': False}, {'confirm': 'yes'}, {'confirm': True, 'user_id': BOB}):
        assert client.request('DELETE', '/api/account', headers=alice, json=body).status_code == 422
    assert 'account_deletion' in client.get('/api/runtime', headers=alice).json()['features']

    response = client.request('DELETE', '/api/account', headers=alice, json={'confirm': True})
    assert response.status_code == 200, response.text
    assert response.json() == {'deleted': True}
    assert owned_rows(engine, ALICE) == {}
    # The cleanup receipt may return keys in any order.
    [(keys, token)] = removed
    assert sorted(keys) == [f'{ALICE}/item/{"a" * 32}.jpg', f'{ALICE}/thread/{"b" * 32}.jpg']
    assert token == alice['Authorization'].removeprefix('Bearer ')
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
    monkeypatch.setattr('anteroom.services.hosted_photos.remove_objects',
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
    monkeypatch.setattr('anteroom.services.hosted_photos.remove_objects',
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
    from anteroom.services.hosted_database import SellerDatabaseSession
    with SellerDatabaseSession(user_id=BOB, bind=engine) as db:
        assert list(db.scalars(text('SELECT * FROM anteroom_private.account_photo_keys()'))) == [
            f'{BOB}/item/{"a" * 32}.jpg', f'{BOB}/thread/{"b" * 32}.jpg']
    # Without a verified seller claim the functions refuse.
    with engine.connect() as db:
        db.execute(text("SELECT set_config('role', 'anteroom_seller', true)"))
        for function in ('delete_account()', 'account_photo_keys()', 'account_deletion_ready()',
                         'stage_account_deletion()', 'finish_account_deletion()'):
            with pytest.raises(DBAPIError, match='A verified seller is required'), db.begin_nested():
                db.execute(text(f'SELECT anteroom_private.{function}')).all()
    with engine.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM auth.users')) == 2


@pytest.mark.parametrize('plaid_offline', [False, True])
def test_vault_credentials_of_only_the_deleted_seller_go(vault_storage, monkeypatch, plaid_offline):  # noqa: F811
    engine, sessions = vault_storage
    for owner in (ALICE, BOB):
        VaultSecretStore(sessions(owner), owner).set('marketplace-accounts', f'fixture-{owner}')
        VaultSecretStore(sessions(owner), owner).set('ai-api-keys', f'fixture-ai-{owner}')
        with sessions(owner)() as db:
            item = PlaidConnection(institution_id='bank', institution_name='Bank', environment='sandbox', accounts=[])
            db.add(item)
            db.add(PlaidTransaction(import_key='expense', imported_revision=1))
            db.commit()
            slot = f'plaid:{item.id}'
        VaultSecretStore(sessions(owner), owner).set(slot, f'fixture-plaid-{owner}')
        if owner == BOB:
            bob_slot = slot
        else:
            alice_slot = slot
    calls = []
    checked_out = set()

    @event.listens_for(engine, 'checkout')
    def checkout(_connection, record, _proxy):
        checked_out.add(record)

    @event.listens_for(engine, 'checkin')
    def checkin(_connection, record):
        checked_out.remove(record)

    async def post(_client, path, *, access_token):
        assert not checked_out
        with engine.connect() as db:
            assert db.scalar(text('SELECT anteroom_private.account_write_allowed(:owner)'), {'owner': ALICE}) is False
        calls.append((path, access_token))
        if plaid_offline and len(calls) == 1:
            raise PlaidError()
        if plaid_offline:
            raise PlaidError('ITEM_NOT_FOUND')
        return {}

    from anteroom import database
    monkeypatch.setattr(database, 'engine', engine)
    monkeypatch.setattr(account_deletion, '_read_relay_device', lambda: None)
    monkeypatch.setattr(PlaidClient, 'configured', lambda: PlaidClient('sandbox', 'fixture', 'fixture'))
    monkeypatch.setattr(PlaidClient, 'post', post)
    if plaid_offline:
        with pytest.raises(account_deletion.AccountDeletionRefused, match='Could not revoke') as refused:
            asyncio.run(account_deletion.delete_account(ALICE, 'fixture-jwt'))
        assert refused.value.status == 503
        assert VaultSecretStore(sessions(ALICE), ALICE).get(alice_slot) == f'fixture-plaid-{ALICE}'
        with engine.connect() as db:
            assert db.scalar(text('SELECT count(*) FROM plaid_connections WHERE user_id = :owner'), {'owner': ALICE}) == 1
    asyncio.run(account_deletion.delete_account(ALICE, 'fixture-jwt'))
    assert calls == [('/item/remove', f'fixture-plaid-{ALICE}')] * (2 if plaid_offline else 1)
    with engine.connect() as db:
        names = set(db.scalars(text('SELECT name FROM vault.secrets')))
        assert names == {f'anteroom:{BOB}:marketplace-accounts', f'anteroom:{BOB}:ai-api-keys', f'anteroom:{BOB}:{bob_slot}'}
        for table in ('plaid_connections', 'plaid_transactions'):
            assert list(db.scalars(text(f'SELECT user_id FROM {table}'))) == [BOB]
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

    monkeypatch.setattr('anteroom.services.keychain.get_relay_device', lambda: {'deviceId': 'd', 'token': 't'})
    monkeypatch.setattr('anteroom.services.relay_client.relay_request', relay)
    asyncio.run(account_deletion._forget_relay_device())
    # 401: the relay already forgot this device, as after an earlier attempt.
    reply['status'] = 401
    asyncio.run(account_deletion._forget_relay_device())
    assert calls == [('DELETE', '/studio/device')] * 2
    reply['status'] = 500
    with pytest.raises(account_deletion.AccountDeletionRefused) as refusal:
        asyncio.run(account_deletion._forget_relay_device())
    assert refusal.value.status == 503
    monkeypatch.setattr('anteroom.services.keychain.get_relay_device', lambda: None)
    asyncio.run(account_deletion._forget_relay_device())
    assert len(calls) == 3


def test_photos_are_removed_in_batches_with_the_seller_session(monkeypatch):
    from anteroom.services import hosted_photos
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
    from anteroom.main import app
    assert not any(getattr(route, 'path', None) == '/api/account' for route in app.routes)
    assert TestClient(app).request('DELETE', '/api/account', json={'confirm': True}).status_code in {404, 405}


def test_concurrent_active_job_refuses_without_touching_storage_or_relay(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, engine = cloud
    supabase_schemas(engine)
    item_id = seed(engine, ALICE, job_status='completed')
    storage_calls = []
    relay_calls = []

    monkeypatch.setattr('anteroom.services.hosted_photos.remove_objects',
                        lambda keys, token: storage_calls.append((list(keys), token)))

    async def fail_relay(method, path, **_kwargs):
        relay_calls.append((method, path))
        pytest.fail('Relay must not be called when account deletion is refused by an active job')

    monkeypatch.setattr('anteroom.services.relay_client.relay_request', fail_relay)

    # Simulate a job starting concurrently right before _delete_records acquires
    # pg_advisory_xact_lock and re-checks active jobs.
    def read_relay_and_start_job():
        with Session(engine) as db:
            db.add(Job(user_id=ALICE, conversation_id=item_id, approved_revision_id='r2',
                       listing_snapshot={}, status='dispatched'))
            db.commit()
        return {'deviceId': 'dev-1', 'token': 'tok-1'}

    monkeypatch.setattr(account_deletion, '_read_relay_device', read_relay_and_start_job)
    before = owned_rows(engine, ALICE)

    response = client.request('DELETE', '/api/account', headers=headers(signing_keys), json={'confirm': True})
    assert response.status_code == 409
    assert 'Nothing was deleted' in response.json()['detail']
    assert storage_calls == []
    assert relay_calls == []
    assert owned_rows(engine, ALICE)['conversations'] == before['conversations']
    with engine.connect() as db:
        assert ALICE in set(map(str, db.scalars(text('SELECT id FROM auth.users'))))


@pytest.mark.parametrize("failure", ["storage", "relay"])
def test_cleanup_failure_keeps_targets_and_credentials_for_retry(cloud, signing_keys, monkeypatch, failure):  # noqa: F811
    client, engine = cloud
    supabase_schemas(engine)
    seed(engine, ALICE, job_status='completed')
    device = {'deviceId': 'dev-1', 'token': 'tok-1'}
    monkeypatch.setattr(account_deletion, '_read_relay_device', lambda: device)
    calls = []
    failing = True

    async def relay(*_args, **kwargs):
        assert kwargs['device'] == device
        if failing and failure == 'relay':
            raise RuntimeError('relay offline')
        return type('Reply', (), {'status_code': 200})()

    def remove_storage(keys, _token):
        calls.append(list(keys))
        if failing and failure == 'storage':
            raise RuntimeError('storage unavailable')

    monkeypatch.setattr('anteroom.services.relay_client.relay_request', relay)
    monkeypatch.setattr('anteroom.services.hosted_photos.remove_objects', remove_storage)
    alice = headers(signing_keys)
    response = client.request('DELETE', '/api/account', headers=alice, json={'confirm': True})
    assert response.status_code == 503, response.text
    with engine.connect() as db:
        assert ALICE in set(map(str, db.scalars(text('SELECT id FROM auth.users'))))
        keys = db.scalar(text('SELECT object_keys FROM anteroom_private.account_cleanup WHERE user_id = :owner'),
                         {'owner': ALICE})
        assert sorted(keys) == [f'{ALICE}/item/{"a" * 32}.jpg', f'{ALICE}/thread/{"b" * 32}.jpg']
    # A fresh request must use the durable targets even if Storage enumeration
    # no longer returns them (for example a partial batch delete before failure).
    with engine.begin() as db:
        db.execute(text('DELETE FROM storage.objects WHERE name LIKE :prefix'), {'prefix': ALICE + '/%'})
    failing = False
    again = client.request('DELETE', '/api/account', headers=alice, json={'confirm': True})
    assert again.status_code == 200, again.text
    assert sorted(calls[-1]) == sorted(keys)
    with engine.connect() as db:
        assert ALICE not in set(map(str, db.scalars(text('SELECT id FROM auth.users'))))
        assert db.scalar(text('SELECT object_keys FROM anteroom_private.account_cleanup WHERE user_id = :owner'),
                         {'owner': ALICE}) == []
    assert owned_rows(engine, ALICE) == {}


def test_pending_deletion_fences_late_import_and_preserves_other_seller(cloud):  # noqa: F811
    from anteroom.services.hosted_database import SellerDatabaseSession
    _client, engine = cloud
    supabase_schemas(engine)
    seed(engine, ALICE)
    account_deletion._delete_records(ALICE)
    for finalize in (False, True):
        if finalize:
            account_deletion._finish_deletion(ALICE)
        with SellerDatabaseSession(user_id=ALICE, bind=engine) as db:
            db.add(Conversation(title='late import'))
            with pytest.raises(DBAPIError, match='Account deletion is pending'):
                db.commit()
        with SellerDatabaseSession(user_id=BOB, bind=engine) as db:
            db.add(Conversation(title='Other seller'))
            db.commit()
    assert owned_rows(engine, ALICE) == {}


def test_every_seller_table_carries_the_deletion_fence(cloud):  # noqa: F811
    # A table added after the fence migration must be fenced too, or a
    # still-valid session can write a deleted seller's rows back.
    _client, engine = cloud
    with engine.connect() as db:
        unfenced = db.execute(text("""
            SELECT DISTINCT c.table_name FROM information_schema.columns AS c
            JOIN information_schema.tables AS t
              ON t.table_schema = c.table_schema AND t.table_name = c.table_name
            WHERE c.table_schema = 'public' AND c.column_name = 'user_id' AND t.table_type = 'BASE TABLE'
              AND NOT EXISTS (
                SELECT 1 FROM pg_trigger AS tr JOIN pg_class AS cl ON cl.oid = tr.tgrelid
                JOIN pg_namespace AS ns ON ns.oid = cl.relnamespace
                WHERE ns.nspname = 'public' AND cl.relname = c.table_name
                  AND tr.tgname = 'account_write_guard')
            ORDER BY 1""")).scalars().all()
    assert unfenced == []


@pytest.mark.parametrize('table', ['sessions', 'audit', 'journals'])
def test_pending_deletion_fences_cloud_session_tables(cloud, table):  # noqa: F811
    from anteroom.models.cloud_session import CloudMarketplaceSession, CloudOperationJournal, CloudSessionAudit
    from anteroom.services.hosted_database import SellerDatabaseSession
    row = {
        'sessions': lambda: CloudMarketplaceSession(marketplace='poshmark'),
        'audit': lambda: CloudSessionAudit(marketplace='poshmark', event='connected'),
        'journals': lambda: CloudOperationJournal(marketplace='poshmark', journal_key='late', payload={}),
    }[table]
    _client, engine = cloud
    supabase_schemas(engine)
    seed(engine, ALICE)
    account_deletion._delete_records(ALICE)
    with SellerDatabaseSession(user_id=ALICE, bind=engine) as db:
        db.add(row())
        with pytest.raises(DBAPIError, match='Account deletion is pending'):
            db.commit()


@pytest.mark.parametrize('receipt_state', ['absent', 'pending', 'completed'])
def test_cloud_session_fence_downgrade(migration_engine, receipt_state):  # noqa: F811
    from alembic import command
    from anteroom.models.cloud_session import CloudSessionAudit
    from anteroom.services import schema_migrations
    from anteroom.services.hosted_database import SellerDatabaseSession

    engine = migration_engine
    if receipt_state != 'absent' and engine.dialect.name != 'postgresql':
        pytest.skip('Deletion receipts require Postgres')
    config = schema_migrations.alembic_config(engine.url.render_as_string(hide_password=False))
    command.upgrade(config, 'head')
    if receipt_state == 'absent':
        command.downgrade(config, 'ae814be47ba5')
        assert schema_migrations.recorded_revision(engine) == 'ae814be47ba5'
        return

    with engine.begin() as db:
        db.execute(text('INSERT INTO anteroom_private.account_cleanup (user_id, object_keys) VALUES (:owner, :keys)'),
                   {'owner': ALICE, 'keys': [f'{ALICE}/photo.jpg'] if receipt_state == 'pending' else []})
    with pytest.raises(DBAPIError, match='Deletion fences cannot be removed'):
        command.downgrade(config, 'ae814be47ba5')
    assert schema_migrations.recorded_revision(engine) == schema_migrations.head_revision()
    with SellerDatabaseSession(user_id=ALICE, bind=engine) as db:
        db.add(CloudSessionAudit(marketplace='poshmark', event='connected'))
        with pytest.raises(DBAPIError, match='Account deletion is pending'):
            db.commit()


def test_storage_trigger_serializes_with_deletion(migration_engine):  # noqa: F811
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from anteroom.services import schema_migrations
    from anteroom.services.hosted_database import SellerDatabaseSession
    engine = migration_engine
    if engine.dialect.name != 'postgresql':
        pytest.skip('Deletion fences require Postgres')
    # Add the managed Storage stand-in after older Storage-policy migrations;
    # the new fence migration must attach to an existing Storage table.
    from alembic import command
    command.upgrade(schema_migrations.alembic_config(engine.url.render_as_string(hide_password=False)), 'b81d24e7a930')
    with engine.begin() as db:
        db.execute(text('CREATE SCHEMA storage; CREATE TABLE storage.objects (bucket_id text, name text)'))
    schema_migrations.ensure_schema(engine)
    started = Event()
    key = f'{ALICE}/item/{"a" * 32}.jpg'
    with engine.connect() as writer, ThreadPoolExecutor() as workers:
        writer.execute(text("INSERT INTO storage.objects VALUES ('anteroom-photos', :key)"), {'key': key})

        def stage():
            with SellerDatabaseSession(user_id=ALICE, bind=engine) as db:
                started.set()
                db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))'),
                           {'identity': f'anteroom-account-delete:{ALICE}'})
                keys = list(db.scalars(text('SELECT * FROM anteroom_private.stage_account_deletion()')))
                db.commit()
                return keys

        pending = workers.submit(stage)
        assert started.wait(5)
        with engine.begin() as probe:
            assert probe.scalar(
                text('SELECT pg_try_advisory_xact_lock(hashtextextended(:identity, 0))'),
                {'identity': f'anteroom-account-delete:{ALICE}'},
            ) is False
        assert not pending.done()
        writer.commit()
        assert pending.result(timeout=10) == [key]
    with engine.begin() as db:
        with pytest.raises(DBAPIError, match='Account deletion is pending'):
            db.execute(text("INSERT INTO storage.objects VALUES ('anteroom-photos', :key)"), {'key': key + '-late'})


def test_concurrent_seller_writes_do_not_serialize_under_deletion_fence(cloud):  # noqa: F811
    from anteroom.services.hosted_database import SellerDatabaseSession
    _client, engine = cloud
    with SellerDatabaseSession(user_id=ALICE, bind=engine) as tx1:
        tx1.add(Conversation(title='First concurrent write'))
        tx1.flush()
        with SellerDatabaseSession(user_id=ALICE, bind=engine) as tx2:
            tx2.execute(text("SET LOCAL lock_timeout = '2s'"))
            tx2.add(Conversation(title='Second concurrent write'))
            tx2.commit()
        tx1.commit()
    assert owned_rows(engine, ALICE)['conversations'] == 2



@pytest.mark.parametrize('phase', ['_delete_records', '_finish_deletion'])
def test_deadlock_rolls_back_and_retries_database_phase(cloud, monkeypatch, phase):  # noqa: F811
    from anteroom.services.hosted_database import SellerDatabaseSession

    _client, engine = cloud
    supabase_schemas(engine)
    seed(engine, ALICE)
    keys = []
    if phase == '_finish_deletion':
        keys = account_deletion._delete_records(ALICE)
    original_commit = SellerDatabaseSession.commit
    attempts = []

    def commit(db):
        attempts.append(db)
        if len(attempts) == 1:
            # Raise the server's deadlock SQLSTATE inside the transaction after
            # its writes, so the retry must survive a real aborted transaction.
            db.execute(text("DO $$ BEGIN RAISE EXCEPTION 'injected deadlock' USING ERRCODE = '40P01'; END $$"))
        return original_commit(db)

    monkeypatch.setattr(SellerDatabaseSession, 'commit', commit)
    result = getattr(account_deletion, phase)(ALICE)
    assert len(attempts) == 2 and attempts[0] is not attempts[1]
    with engine.connect() as db:
        receipt = db.scalar(text('SELECT object_keys FROM anteroom_private.account_cleanup WHERE user_id = :owner'),
                            {'owner': ALICE})
        if phase == '_delete_records':
            assert sorted(result) == sorted(receipt)
            assert len(receipt) == 2
            assert db.scalar(text('SELECT count(*) FROM auth.users WHERE id = :owner'), {'owner': ALICE}) == 1
        else:
            assert keys and receipt == []
            assert db.scalar(text('SELECT count(*) FROM auth.users WHERE id = :owner'), {'owner': ALICE}) == 0
    assert owned_rows(engine, ALICE) == {}
