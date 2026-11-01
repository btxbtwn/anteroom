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
from vendoo_studio.database import Base, load_models
from vendoo_studio.models.ownership import LOCAL_USER_ID
from vendoo_studio.services import account_import, account_move, hosted_photos
from vendoo_studio.services.hosted_context import VaultSecretStore
from vendoo_studio.services.photo_store import LocalPhotoStore


def png() -> bytes:
    image = io.BytesIO()
    Image.new('RGBA', (300, 200), (255, 0, 0, 128)).save(image, 'PNG')
    return image.getvalue()


@pytest.fixture
def mac(tmp_path, monkeypatch):
    """A Mac's own SQLite database and photo folder, with the local owner on every row."""
    from vendoo_studio.models.automation import Automation
    from vendoo_studio.models.conversation import Conversation, Message, Photo
    from vendoo_studio.models.job import Job
    from vendoo_studio.models.listing import Listing
    from vendoo_studio.models.promotion_automation import PromotionAutomation
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
    client, engine = cloud
    secrets = {'ai-api-keys': json.dumps({'openai': 'sk-mac', 'anthropic': 'sk-ant-mac'}),
               'brave-search-api-key': 'brave-mac', 'marketplace-accounts': '{"ebay": {}}', 'relay-device': '{}'}
    monkeypatch.setattr(account_move, 'local_secrets', lambda slots: {slot: secrets[slot] for slot in secrets if slot in slots})
    monkeypatch.setattr('vendoo_studio.services.user_settings.read_settings', lambda: {'ships_from_zip': '10001'})
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
    from vendoo_studio.models.automation import Automation
    from vendoo_studio.models.promotion_automation import PromotionAutomation
    with Session(mac[0]) as db:
        assert db.scalar(select(Automation.status)) == 'paused'
        assert db.scalar(select(PromotionAutomation.enabled)) is False
    assert json.loads(account_move.MARKER.read_text())['user_id'] == USER_ID

    # Running it again copies nothing new and duplicates nothing.
    again = account_move.move('seller@example.com', 'pw', account_for(client, signing_keys), source=mac[0], photos=mac[1])
    assert again['photos'] == 0 and again['missing_photos'] == 1 and again['refused_photos'] == 1 and again['keys'] == []
    assert len(client.get('/api/conversations', headers=alice).json()) == 1
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
    for table in ('background_tasks', 'user_secret_references', 'category_trees', 'nope'):
        assert client.post('/api/account-import/rows', headers=alice, json={'table': table, 'rows': []}).status_code == 422
    assert client.put('/api/account-import/secrets', headers=alice, json={'marketplace-accounts': '{}'}).status_code == 422
    assert client.post('/api/account-import/rows', json={'table': 'conversations', 'rows': [row]}).status_code == 401


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
    from vendoo_studio.main import app
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
