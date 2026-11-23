"""Moving a Mac's listings, photos, settings and keys into the seller's account."""
import io
import json
import time
from datetime import UTC, date, datetime

import pytest
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, session_token, signing_keys  # noqa: F401
from anteroom.database import Base, load_models
from anteroom.models.ownership import LOCAL_USER_ID
from anteroom.services import account_import, account_move, hosted_photos
from anteroom.services.hosted_context import VaultSecretStore
from anteroom.services.photo_store import LocalPhotoStore


def png() -> bytes:
    image = io.BytesIO()
    Image.new('RGBA', (300, 200), (255, 0, 0, 128)).save(image, 'PNG')
    return image.getvalue()


@pytest.fixture
def mac(tmp_path, monkeypatch):
    """A Mac's own SQLite database and photo folder, with the local owner on every row."""
    from anteroom.models.automation import Automation
    from anteroom.models.conversation import Conversation, Message, Photo
    from anteroom.models.job import Job
    from anteroom.models.listing import Listing
    from anteroom.models.promotion_automation import PromotionAutomation
    load_models()
    engine = create_engine(f"sqlite:///{tmp_path / 'mac.db'}")
    Base.metadata.create_all(engine)
    photos = tmp_path / 'photos'
    photos.mkdir()
    (photos / 'coat.png').write_bytes(png())
    (photos / 'coat-original.png').write_bytes(png())
    (photos / 'broken.png').write_bytes(b'not a photo')
    big = io.BytesIO()
    Image.new('RGB', (6000, 6000), 'navy').save(big, 'JPEG', quality=60)
    (photos / 'big.jpeg').write_bytes(big.getvalue())
    monkeypatch.setattr(account_move, 'MARKER', tmp_path / 'account-move.json')
    with Session(engine) as db:
        db.add(Conversation(id='coat0000mac1', title='Wool coat', status='active'))
        db.flush()
        db.add_all([
            Listing(conversation_id='coat0000mac1', validation_status='valid'),
            Photo(id='photo000mac1', conversation_id='coat0000mac1', original_filename='coat.png',
                  stored_filename='coat.png', source_filename='coat-original.png', mime_type='image/png', size_bytes=10),
            Photo(id='photo000mac2', conversation_id='coat0000mac1', original_filename='gone.png',
                  stored_filename='gone.png', mime_type='image/png', size_bytes=10),
            Photo(id='photo000mac3', conversation_id='coat0000mac1', original_filename='broken.png',
                  stored_filename='broken.png', mime_type='image/png', size_bytes=11),
            Photo(id='photo000mac4', conversation_id='coat0000mac1', original_filename='big.jpeg',
                  stored_filename='big.jpeg', mime_type='image/jpeg', size_bytes=len(big.getvalue())),
            Message(conversation_id='coat0000mac1', role='user', text='Measure the sleeves'),
            Job(id='job00000mac1', conversation_id='coat0000mac1', approved_revision_id='r1', listing_snapshot={}, status='queued'),
            Automation(id='auto0000mac1', name='Weekly drop', status='active', schedule={}, filters={}, actions=[], marketplaces=[],
                       created_at=datetime(2026, 10, 1, 12, 30), updated_at=datetime(2026, 10, 1, 12, 30)),
            PromotionAutomation(marketplace='poshmark', action='share', name='Share closet', enabled=True),
        ])
        db.commit()
    yield engine, LocalPhotoStore(photos)
    engine.dispose()


@pytest.fixture
def vault(monkeypatch):
    # CI's Postgres has no Supabase Vault; keep each seller's secrets apart in memory.
    store = {}
    monkeypatch.setattr(VaultSecretStore, 'get', lambda self, account: store.get((self.owner, account)))
    monkeypatch.setattr(VaultSecretStore, 'set', lambda self, account, value: store.__setitem__((self.owner, account), value) or True)
    monkeypatch.setattr(VaultSecretStore, 'delete', lambda self, account: store.pop((self.owner, account), None))
    monkeypatch.setattr(VaultSecretStore, 'update', lambda self, account, mutator: store.__setitem__((self.owner, account), mutator(store.get((self.owner, account)))))
    return store


@pytest.fixture
def storage(monkeypatch):
    stored = {}

    def call(method, key, token, data=None):
        if method == 'POST':
            stored[key] = data
        return stored.get(key, b'')
    monkeypatch.setattr(hosted_photos, 'storage_call', call)
    return stored


def account_for(client, keys, user=USER_ID):
    account = account_move.Account('', client=client)

    def sign_in(email, password):
        account.session = {'access_token': session_token(keys, sub=user), 'refresh_token': 'unused',
                           'expires_at': time.time() + 3600, 'user': {'id': user, 'email': email}}
        return account.session['user']
    account.sign_in = sign_in
    return account


def test_a_mac_moves_into_its_sellers_account_once(cloud, mac, vault, storage, signing_keys, monkeypatch):  # noqa: F811
    from datetime import date
    from decimal import Decimal

    from anteroom.models.business_expense import BusinessExpense

    client, engine = cloud
    with Session(mac[0]) as db:
        db.add(BusinessExpense(incurred_date=date(2026, 1, 10), amount_cents=725, currency="USD",
                               category="mileage", vendor="", description="Sourcing trip", notes="",
                               accounting_type="operating_expense", distance=Decimal(10), distance_unit="mi",
                               mileage_rate_cents=Decimal("72.5"), import_source="manual"))
        db.commit()
    secrets = {'ai-api-keys': json.dumps({'openai': 'sk-mac', 'anthropic': 'sk-ant-mac'}),
               'brave-search-api-key': 'brave-mac', 'marketplace-accounts': '{"ebay": {}}', 'relay-device': '{}'}
    monkeypatch.setattr(account_move, 'local_secrets', lambda slots: {slot: secrets[slot] for slot in secrets if slot in slots})
    monkeypatch.setattr('anteroom.services.user_settings.read_settings', lambda: {'ships_from_zip': '10001'})
    # The account already has its own OpenAI key; the Mac's must not replace it.
    vault[(USER_ID, 'ai-api-keys')] = json.dumps({'openai': 'sk-web'})

    result = account_move.move('seller@example.com', 'pw', account_for(client, signing_keys), source=mac[0], photos=mac[1])

    # An unreadable file is left out without stopping the move; a 36 megapixel
    # photo arrives at the stored size, whatever the account's upload limit.
    assert result['photos'] == 3 and result['missing_photos'] == 1 and result['refused_photos'] == 1
    assert result['paused_automations'] == 2
    assert sorted(result['keys']) == ['ai-api-keys', 'brave-search-api-key']
    assert json.loads(vault[(USER_ID, 'ai-api-keys')]) == {'openai': 'sk-web', 'anthropic': 'sk-ant-mac'}
    # eBay and Etsy reconnect in the account rather than sharing the Mac's tokens.
    assert (USER_ID, 'marketplace-accounts') not in vault and (USER_ID, 'relay-device') not in vault
    alice = headers(signing_keys)
    inventory = client.get('/api/conversations', headers=alice).json()
    assert [row['title'] for row in inventory] == ['Wool coat']
    with engine.connect() as connection:
        owners = connection.execute(select(account_import._table('photos').c.user_id)).scalars().all()
        job = connection.execute(select(account_import._table('jobs').c.status)).scalar_one()
        stored, source = connection.execute(select(account_import._table('photos').c.stored_filename,
                                                   account_import._table('photos').c.source_filename)
                                            .where(account_import._table('photos').c.id == 'photo000mac1')).one()
        created = connection.execute(select(account_import._table('automations').c.created_at)).scalar_one()
    assert set(owners) == {USER_ID} and LOCAL_USER_ID not in owners
    assert job == 'cancelled'  # Mac work in progress stops rather than resuming in the account.
    assert created == datetime(2026, 10, 1, 12, 30)
    for key in (stored, source):
        hosted_photos.check_key(key, USER_ID, 'coat0000mac1')
        assert Image.open(io.BytesIO(storage[key])).format == 'JPEG'
    with engine.connect() as connection:
        big_key = connection.execute(select(account_import._table('photos').c.stored_filename)
                                     .where(account_import._table('photos').c.id == 'photo000mac4')).scalar_one()
    assert Image.open(io.BytesIO(storage[big_key])).size == (2400, 2400)
    photo = client.get('/api/conversations/coat0000mac1/photos/photo000mac1/bytes', headers=alice)
    assert photo.status_code == 200 and photo.content == storage[stored]
    assert client.get('/api/settings', headers=alice).status_code in {200, 404}
    # The Mac keeps its listings as a backup but stops running its automations.
    from anteroom.models.automation import Automation
    from anteroom.models.promotion_automation import PromotionAutomation
    with Session(mac[0]) as db:
        assert db.scalar(select(Automation.status)) == 'paused'
        assert db.scalar(select(PromotionAutomation.enabled)) is False
    assert json.loads(account_move.MARKER.read_text())['user_id'] == USER_ID

    # Running it again copies nothing new and duplicates nothing.
    again = account_move.move('seller@example.com', 'pw', account_for(client, signing_keys), source=mac[0], photos=mac[1])
    assert again['photos'] == 0 and again['missing_photos'] == 1 and again['refused_photos'] == 1 and again['keys'] == []
    assert len(client.get('/api/conversations', headers=alice).json()) == 1
    expenses = client.get('/api/business-expenses', headers=alice,
                          params={'start': '2026-01-01', 'end': '2026-01-31'}).json()
    assert [(expense['description'], expense['amount_cents']) for expense in expenses] == [('Sourcing trip', 725)]
    assert len(storage) == 3

    # The same Mac cannot also move into a second account: its records are taken.
    with pytest.raises(account_move.MoveError, match='already in a different account'):
        account_move.move('bob@example.com', 'pw', account_for(client, signing_keys, BOB), source=mac[0], photos=mac[1])
    assert client.get('/api/conversations', headers=headers(signing_keys, BOB)).json() == []


def test_import_rows_never_trust_the_mac_for_ownership_or_fields(cloud, signing_keys):  # noqa: F811
    client, _ = cloud
    alice = headers(signing_keys)
    row = {'id': 'conv0000own1', 'title': 'Hat', 'user_id': BOB}
    assert client.post('/api/account-import/rows', headers=alice, json={'table': 'conversations', 'rows': [row]}).json() == {'inserted': 1, 'existing': 0}
    assert client.get('/api/conversations', headers=headers(signing_keys, BOB)).json() == []
    assert client.get('/api/conversations', headers=alice).json()[0]['title'] == 'Hat'
    newer = client.post('/api/account-import/rows', headers=alice, json={'table': 'conversations', 'rows': [{'id': 'x', 'future_field': 1}]})
    assert newer.status_code == 409 and 'newer version' in newer.json()['detail']
    for table in ('background_tasks', 'user_secret_references', 'plaid_connections', 'plaid_transactions', 'category_trees', 'nope'):
        assert client.post('/api/account-import/rows', headers=alice, json={'table': table, 'rows': []}).status_code == 422
    assert client.put('/api/account-import/secrets', headers=alice, json={'marketplace-accounts': '{}'}).status_code == 422
    assert client.post('/api/account-import/rows', json={'table': 'conversations', 'rows': [row]}).status_code == 401


def test_import_secrets_keeps_a_concurrent_cloud_key_save(cloud, vault, signing_keys, monkeypatch):  # noqa: F811
    client, _ = cloud
    update = VaultSecretStore.update

    def with_concurrent_save(store, slot, mutator):
        # A seller save commits before the import takes the Vault slot lock.
        vault[(store.owner, slot)] = json.dumps({'openai': 'new-cloud-key'})
        update(store, slot, mutator)

    monkeypatch.setattr(VaultSecretStore, 'update', with_concurrent_save)
    response = client.put('/api/account-import/secrets', headers=headers(signing_keys), json={
        'ai-api-keys': json.dumps({'openai': 'mac-key', 'anthropic': 'another-mac-key'}),
    })
    assert response.status_code == 200
    assert response.json() == {'saved': ['ai-api-keys']}
    assert json.loads(vault[(USER_ID, 'ai-api-keys')]) == {
        'openai': 'new-cloud-key',
        'anthropic': 'another-mac-key',
    }


def test_move_restores_legacy_vendoo_labels_from_the_current_listing(cloud, mac, storage, signing_keys, monkeypatch):  # noqa: F811
    from anteroom.models.conversation import Conversation
    from anteroom.models.listing import Listing, ListingRevision

    client, _ = cloud
    catalog = mac[1].root.parent / 'vendoo-label-catalog.json'
    catalog.write_text(json.dumps({'by_id': {'aBcDeFgHiJkLmNoP1234': 'Bin 4'}}))
    monkeypatch.setattr(account_move, 'DATA_DIR', str(catalog.parent))
    monkeypatch.setattr(account_move, 'local_secrets', lambda slots: {})
    seller_label = 'WinterCollectionSale'
    unmapped_id = 'qRsTuVwXyZaBcDeF5678'
    cases = {
        'legacy': ({'vendooItemId': 'old-import', 'sellerNotes': 'Keep me'},
                   ['Bin 4', 'Winter', seller_label, unmapped_id]),
        'edited': ({'vendooItemId': 'edited-import', 'vendooLabels': 'Seller edit'}, ['Seller edit']),
        'cleared': ({'vendooItemId': 'cleared-import', 'vendooLabels': ''}, []),
        'local': ({'sellerNotes': 'Created locally'}, []),
    }
    with Session(mac[0]) as db:
        for item, (notes, _) in cases.items():
            db.add_all([
                Conversation(id=item, title=item, notes=json.dumps(notes)),
                Listing(conversation_id=item, current_revision_id=f'{item}-current'),
                ListingRevision(id=f'{item}-old', conversation_id=item, source='vendoo_import',
                                listing_json={'labels': ['Old label']}),
                ListingRevision(id=f'{item}-current', conversation_id=item, source='vendoo_import',
                                listing_json={'labels': ['aBcDeFgHiJkLmNoP1234', 'Winter', seller_label, unmapped_id]}),
            ])
        db.commit()

    account = account_for(client, signing_keys)
    account_move.move('seller@example.com', 'pw', account, source=mac[0], photos=mac[1])
    alice = headers(signing_keys)
    inventory = {row['id']: row for row in client.get('/api/conversations', headers=alice).json()}
    for item, (_, labels) in cases.items():
        assert inventory[item]['vendoo_labels'] == labels
    detail = client.get('/api/conversations/legacy', headers=alice)
    assert detail.status_code == 200
    assert json.loads(detail.json()['notes']) == {
        **cases['legacy'][0], 'vendooLabels': f'Bin 4, Winter, {seller_label}, {unmapped_id}',
    }
    with Session(mac[0]) as db:
        assert db.scalar(select(Conversation.notes).where(Conversation.id == 'legacy')) == json.dumps(cases['legacy'][0])


def test_move_fills_missing_cloud_labels_without_replacing_cloud_data(
    cloud, mac, storage, signing_keys, monkeypatch,  # noqa: F811
):
    from anteroom.models.conversation import Conversation
    from anteroom.models.listing import Listing, ListingRevision

    client, engine = cloud
    monkeypatch.setattr(account_move, 'local_secrets', lambda slots: {})
    cloud_notes = {
        'vendooItemId': 'vendoo-item',
        'sellerNotes': 'Cloud edit',
        'vendooDates': {'sold': '1970-01-01'},
    }
    cases = {
        'missing': (cloud_notes, {**cloud_notes, 'vendooLabels': 'clothing, raghouse'}),
        'edited': ({**cloud_notes, 'vendooLabels': 'Cloud label'}, None),
        'cleared': ({**cloud_notes, 'vendooLabels': ''}, None),
        'unknown': ({**cloud_notes, 'vendooLabels': None}, None),
        'different': ({**cloud_notes, 'vendooItemId': 'different-item'}, None),
        'unbound': ({'sellerNotes': 'Cloud edit'}, None),
    }
    with Session(mac[0]) as db:
        for item in cases:
            db.add(Conversation(id=item, title='Desktop title',
                                notes=json.dumps({'vendooItemId': 'vendoo-item', 'sellerNotes': 'Desktop edit'})))
        db.flush()
        for item in cases:
            db.add_all([
                Listing(conversation_id=item, current_revision_id=f'{item}-mac'),
                ListingRevision(id=f'{item}-mac', conversation_id=item, source='vendoo_import',
                                listing_json={'labels': ['clothing', 'raghouse']}),
            ])
        db.commit()
    with Session(engine) as db:
        for item, (notes, _) in cases.items():
            db.add(Conversation(id=item, user_id=USER_ID, title='Cloud title', notes=json.dumps(notes)))
        db.flush()
        db.add_all([
            Listing(conversation_id='missing', user_id=USER_ID, current_revision_id='cloud-revision'),
            ListingRevision(id='cloud-revision', conversation_id='missing', user_id=USER_ID,
                            source='manual', listing_json={'title': 'Cloud listing'}),
        ])
        db.commit()

    account = account_for(client, signing_keys)
    for _ in range(2):
        account_move.move('seller@example.com', 'pw', account, source=mac[0], photos=mac[1])
        with Session(engine) as db:
            for item, (original, updated) in cases.items():
                row = db.get_one(Conversation, item)
                assert row.title == 'Cloud title'
                assert json.loads(row.notes or '{}') == (updated if updated is not None else original)
                if updated is None:
                    assert row.notes == json.dumps(original)
            assert db.get_one(Listing, 'missing').current_revision_id == 'cloud-revision'
    detail = client.get('/api/conversations/missing', headers=headers(signing_keys)).json()
    assert detail['vendoo_labels'] == ['clothing', 'raghouse']


def test_the_account_tells_the_mac_where_to_sign_in(cloud, monkeypatch):  # noqa: F811
    client, _ = cloud
    monkeypatch.setenv('SUPABASE_URL', 'https://project.supabase.co')
    monkeypatch.setenv('SUPABASE_PUBLISHABLE_KEY', 'sb_publishable_x')
    assert client.get('/api/auth-config').json() == {'supabase_url': 'https://project.supabase.co', 'publishable_key': 'sb_publishable_x'}


def test_rows_become_json_the_account_can_read():
    load_models()
    table = Base.metadata.tables['ad_spend']
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine, tables=[table])
    with engine.begin() as connection:
        connection.execute(table.insert().values(id='a1', user_id=LOCAL_USER_ID, marketplace='ebay', spend=1.5, notes='',
                                                 start_date=date(2026, 10, 1), end_date=date(2026, 10, 31),
                                                 created_at=datetime(2026, 10, 1, 8, tzinfo=UTC)))
    with engine.connect() as connection:
        [batch] = list(account_move.batches(connection, table))
    assert batch[0]['start_date'] == '2026-10-01' and batch[0]['created_at'].startswith('2026-10-01T08:00')
    json.dumps(batch)


def test_batches_stay_small(monkeypatch):
    load_models()
    table = Base.metadata.tables['source_boxes']
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine, tables=[table])
    monkeypatch.setattr(account_move, 'BATCH_ROWS', 2)
    with engine.begin() as connection:
        for index in range(5):
            connection.execute(table.insert().values(id=f'box{index}', user_id=LOCAL_USER_ID, store='Goodwill', title=f'Box {index}'))
    with engine.connect() as connection:
        assert [len(batch) for batch in account_move.batches(connection, table)] == [2, 2, 1]


def test_a_failed_sign_in_explains_itself(monkeypatch):
    import httpx

    def handler(request):
        if request.url.path == '/api/auth-config':
            return httpx.Response(200, json={'supabase_url': 'https://project.supabase.co', 'publishable_key': 'k'})
        return httpx.Response(400, json={'error_code': 'invalid_credentials', 'msg': 'Invalid login credentials'})
    account = account_move.Account('https://account.example', client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(account_move.MoveError, match='don’t match'):
        account.sign_in('seller@example.com', 'wrong')


def test_the_mac_reports_what_it_can_move_and_runs_one_move_at_a_time(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from anteroom.main import app
    monkeypatch.setattr(account_move, 'MARKER', tmp_path / 'account-move.json')
    monkeypatch.setattr(account_move, 'local_summary', lambda: {'listings': 3, 'photos': 7})
    monkeypatch.setattr(account_move, '_run', lambda *args: None)
    client = TestClient(app)
    status = client.get('/api/account-move').json()
    assert status['listings'] == 3 and status['photos'] == 7 and status['moved'] is None
    assert status['hosted_url'].startswith('https://')
    started = client.post('/api/account-move', json={'email': 'seller@example.com', 'password': 'pw'})
    assert started.status_code == 202 and started.json()['progress']['state'] == 'running'
    assert 'pw' not in started.text
    assert client.post('/api/account-move', json={'email': 'seller@example.com', 'password': 'pw'}).status_code == 409
    account_move._set(state='idle')


def feed(engine, user=USER_ID):
    from anteroom.models.seller_change import SellerChange
    with engine.connect() as connection:
        return {row.topic: row.changed_at for row in connection.execute(
            select(SellerChange.topic, SellerChange.changed_at).where(SellerChange.user_id == user))}


def test_a_move_announces_one_change_when_it_finishes(cloud, signing_keys):  # noqa: F811
    """Each batch announcing a change made every open device reload the whole
    inventory hundreds of times during a move."""
    client, engine = cloud
    alice = headers(signing_keys)
    for index in range(3):
        rows = [{'id': f'quiet{index:03d}{n:04d}', 'title': f'Item {index}-{n}'} for n in range(5)]
        assert client.post('/api/account-import/rows', headers=alice, json={'table': 'conversations', 'rows': rows}).status_code == 200
    assert client.get('/api/account-import/photos', headers=alice).status_code == 200
    assert feed(engine) == {}
    assert client.post('/api/account-import/finish', headers=alice).status_code == 200
    from anteroom.models.seller_change import TOPIC_BY_TABLE
    assert set(feed(engine)) == set(TOPIC_BY_TABLE.values()) - {None}
    assert feed(engine, BOB) == {}
    # Ordinary edits still announce right away.
    before = feed(engine)['listings']
    client.post('/api/conversations', headers=alice, json={'title': 'New after the move'})
    assert feed(engine)['listings'] > before


def _imported_photo(client, alice):
    rows = [{'id': 'lockcoat0001', 'title': 'Coat'}]
    assert client.post('/api/account-import/rows', headers=alice, json={'table': 'conversations', 'rows': rows}).status_code == 200
    photo = {'id': 'lockphoto001', 'conversation_id': 'lockcoat0001', 'original_filename': 'coat.png',
             'stored_filename': 'coat.png', 'mime_type': 'image/png', 'size_bytes': 10}
    assert client.post('/api/account-import/rows', headers=alice, json={'table': 'photos', 'rows': [photo]}).status_code == 200


def _fake_storage(monkeypatch, on_upload=lambda: None):
    stored = {}

    def call(method, key, token, data=None):
        if method == 'POST':
            on_upload()
            stored[key] = data
        elif method == 'DELETE':
            stored.pop(key, None)
        return stored.get(key, b'')
    monkeypatch.setattr(hosted_photos, 'storage_call', call)
    return stored


def test_a_photo_upload_holds_no_row_lock_while_storage_answers(cloud, signing_keys, monkeypatch):  # noqa: F811
    """Storage can be slow. The photo row is locked only to save its key, after the upload."""
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    client, engine = cloud
    alice = headers(signing_keys)
    _imported_photo(client, alice)
    locked = []

    def check_lock():
        with engine.connect() as other:
            try:
                other.execute(text("SELECT id FROM photos WHERE id = 'lockphoto001' FOR UPDATE NOWAIT"))
                locked.append(False)
            except OperationalError:
                locked.append(True)
    stored = _fake_storage(monkeypatch, check_lock)

    reply = client.post('/api/account-import/photos/lockphoto001/stored', headers=alice, content=png())
    assert reply.status_code == 200 and reply.json() == {'copied': True}
    assert locked == [False]
    with engine.connect() as connection:
        key = connection.execute(text("SELECT stored_filename FROM photos WHERE id = 'lockphoto001'")).scalar_one()
    assert set(stored) == {key}
    # Already in the account: nothing is uploaded again.
    again = client.post('/api/account-import/photos/lockphoto001/stored', headers=alice, content=png())
    assert again.json() == {'copied': False} and len(locked) == 1


def test_a_refused_photo_save_deletes_the_uploaded_object(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, _engine = cloud
    alice = headers(signing_keys)
    _imported_photo(client, alice)
    stored = _fake_storage(monkeypatch)

    def refuse(db, photo_id, field, key, data, width, height):
        raise RuntimeError('refused')
    monkeypatch.setattr(account_import, 'save_imported_photo', refuse)

    with pytest.raises(RuntimeError, match='refused'):
        client.post('/api/account-import/photos/lockphoto001/stored', headers=alice, content=png())
    assert stored == {}


def test_photo_upload_429_retries_outlast_slow_same_seller_body_read(monkeypatch):
    """Sibling uploads in _move_photos retry HTTP 429 across BODY_RECEIVE_SECONDS without extending 503 retries."""
    import httpx
    from anteroom.services.hosted_limits import BODY_RECEIVE_SECONDS

    sleeps: list[float] = []
    monkeypatch.setattr(account_move.time, 'sleep', sleeps.append)

    # 1. Survives more than 3 consecutive 429s covering a full BODY_RECEIVE_SECONDS read.
    calls = 0

    def slow_slot_handler(request):
        nonlocal calls
        calls += 1
        if calls <= 12:
            return httpx.Response(429, headers={'Retry-After': '5'}, json={'detail': 'Another upload is running.'})
        return httpx.Response(200, json={'copied': True})

    account = account_move.Account('https://account.example', client=httpx.Client(transport=httpx.MockTransport(slow_slot_handler)))
    account.session = {'access_token': 'tok', 'refresh_token': 'ref', 'expires_at': time.time() + 3600}
    reply = account.request(
        'POST',
        '/api/account-import/photos/p1/stored',
        rate_limit_budget=account_move.PHOTO_RATE_LIMIT_BUDGET_SECONDS,
    )
    assert reply.status_code == 200 and calls == 13
    assert sleeps == [5.0] * 12

    # 2. Stops retrying 429 once the permitted upload budget is exhausted.
    sleeps.clear()
    always_429_calls = 0

    def always_429(request):
        nonlocal always_429_calls
        always_429_calls += 1
        return httpx.Response(429, headers={'Retry-After': '5'}, json={'detail': 'Still busy.'})

    bounded = account_move.Account('https://account.example', client=httpx.Client(transport=httpx.MockTransport(always_429)))
    bounded.session = {'access_token': 'tok', 'refresh_token': 'ref', 'expires_at': time.time() + 3600}
    with pytest.raises(account_move.MoveError, match='Still busy.') as exc:
        bounded.request(
            'POST',
            '/api/account-import/photos/p1/stored',
            rate_limit_budget=account_move.PHOTO_RATE_LIMIT_BUDGET_SECONDS,
        )
    assert exc.value.status == 429
    assert BODY_RECEIVE_SECONDS <= sum(sleeps) <= account_move.PHOTO_RATE_LIMIT_BUDGET_SECONDS

    # 3. Non-429 transient errors (e.g. 503) preserve the 3-attempt limit even when rate_limit_budget is set.
    sleeps.clear()
    error_503_calls = 0

    def always_503(request):
        nonlocal error_503_calls
        error_503_calls += 1
        return httpx.Response(503, json={'detail': 'Service unavailable'})

    transient = account_move.Account('https://account.example', client=httpx.Client(transport=httpx.MockTransport(always_503)))
    transient.session = {'access_token': 'tok', 'refresh_token': 'ref', 'expires_at': time.time() + 3600}
    with pytest.raises(account_move.MoveError, match='Service unavailable') as exc503:
        transient.request(
            'POST',
            '/api/account-import/photos/p1/stored',
            rate_limit_budget=account_move.PHOTO_RATE_LIMIT_BUDGET_SECONDS,
        )
    assert exc503.value.status == 503
    assert error_503_calls == 3 and sleeps == [1, 2]

    # 4. _move_photos wires the extended 429 budget end-to-end.
    sleeps.clear()
    load_models()
    from anteroom.models.conversation import Conversation, Photo
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Conversation(id='c429', title='Coat'))
        db.flush()
        db.add(Photo(id='p429', conversation_id='c429', original_filename='a.png',
                     stored_filename='a.png', mime_type='image/png', size_bytes=10))
        db.commit()

    class _FakeStore:
        def exists(self, key):
            return key == 'a.png'

        def read(self, key):
            return png()

    move_calls = 0

    def move_handler(request):
        nonlocal move_calls
        if request.url.path == '/api/account-import/photos':
            return httpx.Response(200, json=[{'id': 'p429', 'fields': ['stored']}])
        move_calls += 1
        if move_calls <= 5:
            return httpx.Response(429, headers={'Retry-After': '5'}, json={'detail': 'Busy'})
        return httpx.Response(200, json={'copied': True})

    mover = account_move.Account('https://account.example', client=httpx.Client(transport=httpx.MockTransport(move_handler)))
    mover.session = {'access_token': 'tok', 'refresh_token': 'ref', 'expires_at': time.time() + 3600}
    try:
        counts = account_move._move_photos(mover, engine, _FakeStore())
    finally:
        engine.dispose()
    assert counts == {'copied': 1, 'missing': 0, 'refused': 0}
    assert move_calls == 6 and sleeps == [5.0] * 5


def test_move_photos_serializes_same_seller_upload_requests():
    """Workers resize in parallel, but only one photo POST runs at a time so siblings never trigger 429."""
    import threading
    import httpx

    load_models()
    from anteroom.models.conversation import Conversation, Photo

    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    photo_ids = [f'pser{i}' for i in range(6)]
    with Session(engine) as db:
        db.add(Conversation(id='cser', title='Coat'))
        db.flush()
        for pid in photo_ids:
            db.add(Photo(id=pid, conversation_id='cser', original_filename=f'{pid}.png',
                         stored_filename=f'{pid}.png', mime_type='image/png', size_bytes=10))
        db.commit()

    raw = png()

    class _FakeStore:
        def exists(self, key):
            return True

        def read(self, key):
            return raw

    counter_lock = threading.Lock()
    in_flight = 0
    max_in_flight = 0
    uploaded = 0

    def handler(request):
        nonlocal in_flight, max_in_flight, uploaded
        if request.url.path == '/api/account-import/photos':
            return httpx.Response(200, json=[{'id': pid, 'fields': ['stored']} for pid in photo_ids])
        with counter_lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        time.sleep(0.02)
        with counter_lock:
            in_flight -= 1
            uploaded += 1
        return httpx.Response(200, json={'copied': True})

    account = account_move.Account('https://account.example', client=httpx.Client(transport=httpx.MockTransport(handler)))
    account.session = {'access_token': 'tok', 'refresh_token': 'ref', 'expires_at': time.time() + 3600}
    try:
        counts = account_move._move_photos(account, engine, _FakeStore())
    finally:
        engine.dispose()
    assert counts == {'copied': len(photo_ids), 'missing': 0, 'refused': 0}
    assert uploaded == len(photo_ids)
    assert max_in_flight == 1



