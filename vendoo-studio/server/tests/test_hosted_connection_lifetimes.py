"""Slow storage and settings reads must not require an extra pooled connection."""
from unittest.mock import MagicMock
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from test_hosted_drafts import cloud, headers  # noqa: F401
from test_hosted_inventory import image_bytes
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.database import Base, load_models
from vendoo_studio.services import hosted_photos
from vendoo_studio.services.hosted_context import HostedSettingsStore
from vendoo_studio.services.service_context import ServiceContext, use
from vendoo_studio.services.suggestions import list_suggestions


@pytest.mark.parametrize('part', ['screen', 'listing', 'photos', 'messages', 'activity', 'marketplace-listings', 'copy-preview'])
def test_editor_reads_release_connections_before_sending_json(cloud, active_connections, signing_keys, monkeypatch, part):  # noqa: F811
    # A route with a response_model sends a plain Response, the rest a JSONResponse.
    from starlette.responses import Response

    client, _engine = cloud
    alice = headers(signing_keys)
    identity = client.post('/api/drafts', headers=alice, json={'title': 'Tee'}).json()['id']
    send_response = Response.__call__
    observed = []

    async def send_without_connection(response, scope, receive, send):
        if scope['path'] == f'/api/conversations/{identity}/{part}':
            observed.append(True)
            assert not active_connections, 'Sending an editor response must not reserve a database connection'
        await send_response(response, scope, receive, send)

    monkeypatch.setattr(Response, '__call__', send_without_connection)
    assert client.get(f'/api/conversations/{identity}/{part}', headers=alice).status_code == 200
    assert observed


def test_inventory_financial_calculations_release_the_read_connection(cloud, active_connections, signing_keys, monkeypatch):  # noqa: F811
    from vendoo_studio.models.marketplace_sale import MarketplaceSale
    from vendoo_studio.services import sales_ledger
    from vendoo_studio.services.hosted_database import SellerDatabaseSession

    client, engine = cloud
    alice = headers(signing_keys)
    identity = client.post('/api/drafts', headers=alice, json={'title': 'Sold tee'}).json()['id']
    with SellerDatabaseSession(bind=engine, user_id=USER_ID) as db:
        db.add(MarketplaceSale(marketplace='ebay', order_id='order', line_id='line',
                               conversation_id=identity, quantity=1,
                               financials={'currency': 'USD', 'item_revenue': '25', 'fees': '3',
                                           'shipping_credit': '0', 'shipping_cost': '4', 'item_cost': '2', 'refunds': '0'}))
        db.commit()
    original = sales_ledger.financial_values
    checked = []

    def calculate(sale):
        assert not active_connections, 'Inventory financial calculations must run after the read session closes'
        checked.append(sale.id)
        return original(sale)

    monkeypatch.setattr(sales_ledger, 'financial_values', calculate)
    response = client.get('/api/conversations', headers=alice)
    assert response.status_code == 200
    assert len(checked) >= 2, 'Both Analytics and ledger conversion must release the connection'
    assert response.json()[0]['missing_sale_data']


@pytest.fixture(params=[5, 8], ids=['bounded-regression', 'production-burst'])
def concurrent_cloud(cloud, monkeypatch, request):  # noqa: F811
    client, database = cloud
    engine = (create_engine(database.url, pool_size=5, max_overflow=0, pool_timeout=2)
              if request.param == 5 else database)
    monkeypatch.setattr('vendoo_studio.database.engine', engine)
    try:
        yield client, request.param
    finally:
        engine.dispose()


def test_three_slow_editors_leave_connections_available_for_another_save(cloud, signing_keys, monkeypatch):  # noqa: F811
    from starlette.responses import JSONResponse

    client, database = cloud
    alice = headers(signing_keys)
    drafts = [client.post('/api/drafts', headers=alice, json={'title': f'Editor {index}'}).json()
              for index in range(4)]
    engine = create_engine(database.url, pool_size=3, max_overflow=0, pool_timeout=0.1)
    monkeypatch.setattr('vendoo_studio.database.engine', engine)
    waiting = threading.Barrier(4, timeout=10)
    release = threading.Event()
    send_response = JSONResponse.__call__

    async def slow_response(response, scope, receive, send):
        if scope['path'].endswith('/screen'):
            await asyncio.to_thread(waiting.wait)
            await asyncio.to_thread(release.wait, 15)
        await send_response(response, scope, receive, send)

    monkeypatch.setattr(JSONResponse, '__call__', slow_response)
    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            responses = [executor.submit(client.get, f"/api/conversations/{draft['id']}/screen", headers=alice)
                         for draft in drafts[:3]]
            try:
                waiting.wait()
                assert client.get('/api/status', headers=alice).status_code == 200
                other = drafts[3]
                saved = client.patch(f"/api/conversations/{other['id']}", headers=alice, json={
                    'notes': 'Saved while three editors are still receiving responses',
                })
                assert saved.status_code == 200
                assert saved.json()['notes'].startswith('Saved while')
            finally:
                release.set()
            assert [response.result().status_code for response in responses] == [200] * 3
    finally:
        engine.dispose()


def test_concurrent_listing_reads_do_not_wait_for_nested_settings_connections(concurrent_cloud, signing_keys, monkeypatch):  # noqa: F811
    from vendoo_studio.routes import hosted_workspace

    client, readers = concurrent_cloud
    alice = headers(signing_keys)
    identity = client.post('/api/drafts', headers=alice, json={'title': 'Tee'}).json()['id']
    ready = threading.Barrier(readers, timeout=10)
    validate = hosted_workspace.validate_listing

    def together(*args, **kwargs):
        ready.wait()
        return validate(*args, **kwargs)

    monkeypatch.setattr(hosted_workspace, 'validate_listing', together)
    with ThreadPoolExecutor(max_workers=readers) as executor:
        responses = list(executor.map(
            lambda _: client.get(f'/api/conversations/{identity}/listing', headers=alice),
            range(readers),
        ))
    assert [response.status_code for response in responses] == [200] * readers
    assert all(response.json()['listing']['title'] == 'Tee' for response in responses)


def test_settings_snapshot_failure_returns_a_private_retriable_response(cloud, signing_keys, monkeypatch):  # noqa: F811
    from sqlalchemy.exc import SQLAlchemyError

    client, _engine = cloud
    alice = headers(signing_keys)
    identity = client.post('/api/drafts', headers=alice, json={'title': 'Tee'}).json()['id']

    class UnavailableSettings:
        def read(self):
            raise SQLAlchemyError('private-diagnostic')

        def load(self, _db):
            self.read()

    context = ServiceContext(USER_ID, MagicMock(), UnavailableSettings(), MagicMock(), MagicMock())
    monkeypatch.setattr('vendoo_studio.services.hosted_context.for_seller', lambda *_args: context)
    response = client.get(f'/api/conversations/{identity}/listing', headers=alice)
    assert response.status_code == 503
    assert response.headers['retry-after'] == '5'
    assert 'private-diagnostic' not in response.text


@pytest.fixture
def single_connection(tmp_path):
    load_models()
    engine = create_engine(f"sqlite:///{tmp_path / 'single.db'}", poolclass=QueuePool,
                           pool_size=1, max_overflow=0, pool_timeout=0.05)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    try:
        yield sessions
    finally:
        engine.dispose()


def test_category_model_does_not_hold_the_catalog_connection(single_connection, monkeypatch):
    from vendoo_studio.models.catalog import CategoryTree, CategoryTreeNode
    from vendoo_studio.services import category_selection

    with single_connection() as db:
        db.add(CategoryTree(marketplace='general', status='complete'))
        db.add(CategoryTreeNode(marketplace='general', category_id='1', parent_id='', label='Tees',
                               path='Men > Tees', is_leaf=True, has_children=False))
        db.commit()

        def choices(db, *args, **kwargs):
            node = db.get(CategoryTreeNode, ('general', '1'))
            return {'general': [{'id': '1', 'path': node.path}]}, {'general': {'1': node}}

        async def model(*args):
            with single_connection() as other:
                assert other.get(CategoryTree, 'general').status == 'complete'
            return {'categories': {'general': '1'}}

    monkeypatch.setattr(category_selection, '_collect_choices', choices)
    monkeypatch.setattr(category_selection, '_ask_model', model)
    monkeypatch.setattr('vendoo_studio.database.SessionLocal', single_connection)
    result = asyncio.run(category_selection.select_categories(object(), 'Tee', '', []))
    assert result == {'general': 'Men > Tees'}


def test_live_category_lookup_does_not_hold_the_tree_connection(single_connection, monkeypatch):
    from vendoo_studio.models.catalog import CategoryTree
    from vendoo_studio.services import direct_categories, native_category_match

    completed = []

    async def lookup(query):
        with single_connection() as other:
            other.query(CategoryTree).all()
        completed.append(query)
        return None

    monkeypatch.setattr(native_category_match, 'native_rows', lambda: {})
    monkeypatch.setattr(direct_categories, '_ebay_leaf', lookup)
    monkeypatch.setattr(direct_categories, '_etsy_leaf', lookup)
    monkeypatch.setattr('vendoo_studio.database.SessionLocal', single_connection)
    asyncio.run(direct_categories.attach_direct_categories({'title': 'Black cotton tee'}))
    assert len(completed) == 2


def test_generation_provider_lookup_does_not_hold_the_draft_connection(single_connection, monkeypatch):
    from fastapi import HTTPException
    from vendoo_studio.models.conversation import Conversation
    from vendoo_studio.routes import chat

    provider = SimpleNamespace(name='test', vision_model='vision', listing_model='text')

    def configured_provider():
        with single_connection() as other:
            assert other.get(Conversation, 'draft') is not None
        return provider

    monkeypatch.setattr(chat, '_require_provider', configured_provider)
    monkeypatch.setattr(chat, 'get_photo_provider', lambda: provider)
    monkeypatch.setattr('vendoo_studio.database.SessionLocal', single_connection)
    with single_connection() as db:
        db.add(Conversation(id='draft', title='Tee'))
        db.commit()
    with pytest.raises(HTTPException) as error:
        asyncio.run(chat.generate_listing('draft'))
    assert error.value.status_code == 400
    assert 'No photos' in error.value.detail


@pytest.mark.parametrize('running', [True, False])
def test_generation_resume_does_not_reconfigure_the_provider(single_connection, monkeypatch, running):
    from vendoo_studio.models.conversation import Conversation
    from vendoo_studio.routes import chat

    def unexpected_provider():
        raise AssertionError('Reconnecting must not open provider settings or credentials')

    run = object() if running else None
    monkeypatch.setattr(chat, '_require_provider', unexpected_provider)
    monkeypatch.setattr(chat, 'active_generation', lambda _id: run)
    monkeypatch.setattr(chat, 'stream_generation', lambda _run: 'running')
    monkeypatch.setattr(chat, 'stream_finished', lambda: 'finished')
    monkeypatch.setattr('vendoo_studio.database.SessionLocal', single_connection)
    with single_connection() as db:
        db.add(Conversation(id='draft', title='Tee'))
        db.commit()
    assert asyncio.run(chat.generate_listing('draft', resume=True)) == (
        'running' if running else 'finished'
    )


def test_generation_downloads_photos_without_a_draft_connection(single_connection, monkeypatch):
    from vendoo_studio.models.conversation import Conversation, Photo
    from vendoo_studio.routes import chat

    provider = SimpleNamespace(name='test', vision_model='vision', listing_model='text')
    downloaded = []

    def local_paths(filenames):
        with single_connection() as other:
            assert other.get(Conversation, 'draft') is not None
        downloaded.extend(filenames)
        return filenames

    monkeypatch.setattr(chat, '_require_provider', lambda: provider)
    monkeypatch.setattr(chat, 'get_photo_provider', lambda: provider)
    monkeypatch.setattr(chat, 'photo_store', lambda: SimpleNamespace(local_paths=local_paths))
    monkeypatch.setattr(chat, 'start_generation', lambda *_args: object())
    monkeypatch.setattr(chat, 'stream_generation', lambda _run: 'started')
    monkeypatch.setattr('vendoo_studio.database.SessionLocal', single_connection)
    with single_connection() as db:
        db.add(Conversation(id='draft', title='Tee'))
        db.commit()
        db.add(Photo(conversation_id='draft', original_filename='tee.jpg', stored_filename='tee.jpg',
                     mime_type='image/jpeg', size_bytes=1))
        db.commit()
    assert asyncio.run(chat.generate_listing('draft')) == 'started'
    assert downloaded == ['tee.jpg']


def test_suggestions_read_settings_before_holding_the_inventory_connection(tmp_path):
    load_models()
    engine = create_engine(f"sqlite:///{tmp_path / 'pool.db'}", poolclass=QueuePool,
                           pool_size=1, max_overflow=0, pool_timeout=0.05)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    context = ServiceContext(USER_ID, sessions, HostedSettingsStore(sessions), MagicMock(), MagicMock())
    try:
        with use(context), sessions() as db:
            assert list_suggestions(db) == []
    finally:
        engine.dispose()


def test_suggestion_ranking_releases_its_connection_before_cpu_work(single_connection, monkeypatch):
    from sqlalchemy import text
    from vendoo_studio.models.conversation import Conversation
    from vendoo_studio.models.listing import Listing, ListingRevision
    from vendoo_studio.services import suggestions

    with single_connection() as db:
        db.add(Conversation(id='ranked-tee', title='Cotton tee'))
        db.commit()
        db.add(ListingRevision(id='ranked-revision', conversation_id='ranked-tee', source='test',
                               listing_json={'title': 'Cotton tee', 'category_path': 'Men > Shirts > Tees'}))
        db.add(Listing(conversation_id='ranked-tee', current_revision_id='ranked-revision'))
        db.commit()
    seasons = suggestions._seasons
    ranked = []

    def rank_without_holding_connection(*args):
        with single_connection() as other:
            assert other.scalar(text('SELECT 1')) == 1
        ranked.append(True)
        return seasons(*args)

    monkeypatch.setattr('vendoo_studio.services.user_settings.get_stale_listing_days', lambda: 90)
    monkeypatch.setattr(suggestions, '_seasons', rank_without_holding_connection)
    with single_connection() as db:
        cards = suggestions.list_suggestions(db)
        assert [(card['conversation_id'], card['title']) for card in cards] == [('ranked-tee', 'Cotton tee')]
    assert ranked == [True]


@pytest.mark.parametrize('route', ['draft', 'bytes', 'photo', 'thumb'])
def test_photo_reads_return_the_database_connection_before_storage(cloud, active_connections, signing_keys, monkeypatch, route):  # noqa: F811
    client, _engine = cloud
    alice = headers(signing_keys)
    draft = client.post('/api/drafts', headers=alice, json={'title': 'Coat'}).json()['id']
    data = image_bytes()

    def storage(method, *_args):
        if method == 'GET':
            assert not active_connections, 'Private image I/O must not retain the request database connection'
        return data

    monkeypatch.setattr(hosted_photos, 'storage_call', storage)
    uploaded = client.post(f'/api/drafts/{draft}/photos', headers=alice, content=data)
    assert uploaded.status_code == 201, uploaded.text
    photo = uploaded.json()['id']
    paths = {'draft': f'/api/drafts/{draft}/photos/{photo}',
             'bytes': f'/api/conversations/{draft}/photos/{photo}/bytes',
             'photo': f'/api/photos/{photo}', 'thumb': f'/api/photos/{photo}/thumb'}
    assert client.get(paths[route], headers=alice).status_code == 200


def test_a_seller_request_reads_its_settings_through_its_own_connection(cloud, signing_keys):  # noqa: F811
    """A second session for the settings snapshot costs another checkout and four round trips per request."""
    client, engine = cloud
    alice = headers(signing_keys)
    draft = client.post('/api/drafts', headers=alice, json={'title': 'Coat'}).json()['id']
    checkouts, statements = [], []

    def checkout(*_args):
        checkouts.append(True)

    def record(_connection, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(engine, 'checkout', checkout)
    event.listen(engine, 'before_cursor_execute', record)
    try:
        assert client.get(f'/api/drafts/{draft}/photos', headers=alice).status_code == 200
    finally:
        event.remove(engine, 'checkout', checkout)
        event.remove(engine, 'before_cursor_execute', record)
    assert len(checkouts) == 1
    assert sum('user_preferences' in statement for statement in statements) == 1


def test_photo_upload_and_delete_write_storage_without_a_database_connection(cloud, active_connections, signing_keys, monkeypatch):  # noqa: F811
    """Storage can take 20 seconds; the draft's row lock and pooled connection must not wait for it."""
    client, _engine = cloud
    alice = headers(signing_keys)
    draft = client.post('/api/drafts', headers=alice, json={'title': 'Coat'}).json()['id']
    objects, calls = {}, []

    def storage(method, key, _token=None, data=None):
        calls.append((method, bool(active_connections)))
        if method == 'POST':
            objects[key] = data
        elif method == 'DELETE':
            objects.pop(key, None)
        return b''

    monkeypatch.setattr(hosted_photos, 'storage_call', storage)
    uploaded = client.post(f'/api/drafts/{draft}/photos', headers=alice, content=image_bytes())
    assert uploaded.status_code == 201, uploaded.text
    assert len(objects) == 1
    assert client.delete(f"/api/drafts/{draft}/photos/{uploaded.json()['id']}", headers=alice).status_code == 204
    assert objects == {}
    assert calls == [('POST', False), ('DELETE', False)], 'Storage calls must not hold a database connection'


def test_a_refused_photo_upload_removes_its_stored_object(cloud, signing_keys, monkeypatch):  # noqa: F811
    client, _engine = cloud
    alice = headers(signing_keys)
    draft = client.post('/api/drafts', headers=alice, json={'title': 'Coat'}).json()['id']
    objects = {}

    def storage(method, key, _token=None, data=None):
        if method == 'POST':
            objects[key] = data
        elif method == 'DELETE':
            objects.pop(key, None)
        return b''

    monkeypatch.setattr(hosted_photos, 'storage_call', storage)
    monkeypatch.setattr(hosted_photos, 'MAX_PHOTOS', 1)
    assert client.post(f'/api/drafts/{draft}/photos', headers=alice, content=image_bytes()).status_code == 201
    refused = client.post(f'/api/drafts/{draft}/photos', headers=alice, content=image_bytes())
    assert refused.status_code == 422
    assert len(objects) == 1, 'A refused upload must not leave its object in Storage'


@pytest.fixture
def active_connections(cloud):  # noqa: F811
    _client, engine = cloud
    active = set()

    def checkout(connection, *_args):
        active.add(connection)

    def checkin(connection, *_args):
        active.discard(connection)

    event.listen(engine, 'checkout', checkout)
    event.listen(engine, 'checkin', checkin)
    try:
        yield active
    finally:
        event.remove(engine, 'checkout', checkout)
        event.remove(engine, 'checkin', checkin)


@pytest.mark.parametrize('approved', [False, True])
def test_relay_job_photos_release_the_connection_before_private_storage(cloud, active_connections, signing_keys, monkeypatch, approved):  # noqa: F811
    from sqlalchemy.orm import Session
    from vendoo_studio.models.conversation import Photo
    from vendoo_studio.models.job import Job
    from vendoo_studio.services.approved_photos import KEY

    client, engine = cloud
    alice = headers(signing_keys)
    identity = client.post('/api/drafts', headers=alice, json={'title': 'Private draft'}).json()['id']
    data = image_bytes()
    def storage(method, *_args):
        if method == 'GET':
            assert not active_connections, 'Job photo storage must not hold a database connection'
        return data
    monkeypatch.setattr(hosted_photos, 'storage_call', storage)
    uploaded = client.post(f'/api/drafts/{identity}/photos', headers=alice, content=data)
    photo_id = uploaded.json()['id']
    with Session(engine) as db:
        photo = db.get(Photo, photo_id)
        job = Job(user_id=USER_ID, conversation_id=identity, approved_revision_id='', status='dispatched',
            current_step='vendoo_photos', listing_snapshot={KEY: [{'id': photo_id,
                'stored_filename': photo.stored_filename, 'mime_type': photo.mime_type}]} if approved else {})
        db.add(job)
        db.commit()
        job_id = job.id
    response = client.get(f'/api/browser-relay/jobs/{job_id}/photos/{photo_id}', headers=alice)
    assert response.status_code == 200, response.text


def test_generate_releases_the_seller_session_before_streaming(monkeypatch):
    """Keepalives must not retain a seller session for the whole Mac wait."""
    from types import SimpleNamespace
    from vendoo_studio.routes import hosted_tasks
    from vendoo_studio.services.service_context import ServiceContext, use

    closed = []

    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            closed.append('closed')
            return False

        def scalar(self, *_args, **_kwargs):
            return SimpleNamespace(id='task1')

        def rollback(self):
            return None

    monkeypatch.setattr(hosted_tasks, 'task_stream', lambda task_id: f'stream:{task_id}')
    with use(ServiceContext(USER_ID, lambda: DB(), MagicMock(), MagicMock(), MagicMock())):
        assert hosted_tasks.generate('draft', MagicMock()) == 'stream:task1'
    assert closed == ['closed']


def test_submit_returns_the_task_id_for_streaming(monkeypatch):
    from vendoo_studio.routes import hosted_tasks

    class DB:
        def rollback(self):
            return None

    monkeypatch.setattr(hosted_tasks, 'owned_conversation', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hosted_tasks, 'get_listing_provider', lambda: object())
    monkeypatch.setattr(hosted_tasks.ConversationRepo, 'get_photos', lambda *_args, **_kwargs: ['photo.jpg'])
    monkeypatch.setattr(hosted_tasks.queue, 'require_idle', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hosted_tasks, 'store_uploads', lambda *_args, **_kwargs: [])
    monkeypatch.setattr(hosted_tasks.queue, 'draft_payload', lambda *_args, **_kwargs: {})
    monkeypatch.setattr(hosted_tasks.queue, 'enqueue', lambda *_args, **_kwargs: SimpleNamespace(id='task2'))
    assert hosted_tasks.submit(DB(), 'draft', 'generate') == 'task2'


def test_native_editor_form_releases_the_connection_before_marketplace_waits(single_connection, monkeypatch):
    """eBay/Chrome form refreshes must not pin the hosted pool during network I/O."""
    from vendoo_studio.models.conversation import Conversation
    from vendoo_studio.services import native_editor

    released = []

    async def ebay_form(snapshot):
        with single_connection() as other:
            assert other.scalar(__import__('sqlalchemy').text('SELECT 1')) == 1
        released.append(snapshot.get('title'))
        return {'marketplace': 'ebay', 'fields': []}

    monkeypatch.setattr(native_editor, 'ebay_form', ebay_form)
    monkeypatch.setattr(native_editor.direct_preflight, 'prepare',
                        lambda db, conv, listing_json, marketplace: {'title': conv.title})
    with single_connection() as db:
        db.add(Conversation(id='form-tee', title='Cotton tee'))
        db.commit()
    monkeypatch.setattr('vendoo_studio.database.SessionLocal', single_connection)
    form = asyncio.run(native_editor.editor_form('form-tee', 'ebay', None, refresh=True))
    assert form['marketplace'] == 'ebay'
    assert released == ['Cotton tee']


def test_inventory_list_closes_the_seller_session_before_returning(monkeypatch):
    """A large inventory JSON body must not keep a pooled connection checked out."""
    from vendoo_studio.routes import hosted_workspace

    closed = []

    class Result:
        def all(self):
            return []

    class DB:
        def scalars(self, *_args, **_kwargs):
            return Result()

        def execute(self, *_args, **_kwargs):
            return []

        def rollback(self):
            return None

        def close(self):
            closed.append('closed')

    class Repo:
        def __init__(self, db):
            self.db = db

        def reconcile_item_statuses(self, conversations, notes, statuses):
            assert conversations == []
            assert notes == statuses == {}
            return None

        def current_revision_ids(self):
            assert closed == []
            return {}

    def unsent_edits(db, conversations, revisions):
        assert closed == []
        assert conversations == []
        assert revisions == {}
        return set()

    monkeypatch.setattr('vendoo_studio.services.unsent_edits.unsent_edits', unsent_edits)
    monkeypatch.setattr('vendoo_studio.repositories.queries.ConversationRepo', Repo)
    from vendoo_studio.services.inventory_analytics import AnalyticsInputs
    monkeypatch.setattr('vendoo_studio.services.inventory_analytics.read_analytics_inputs',
                        lambda _db: AnalyticsInputs({}, {}, [], {}, {}))
    monkeypatch.setattr('vendoo_studio.services.sales_records.read_sale_lines', lambda _db: [])
    assert hosted_workspace.inventory_response(DB()) == []
    assert closed == ['closed']


def test_sales_read_releases_its_connection_before_account_refresh(cloud, active_connections, signing_keys, monkeypatch):  # noqa: F811
    from vendoo_studio.services import recent_orders

    client, _ = cloud
    refreshed = []

    async def refresh():
        # The real refresh can read credentials and call eBay. Its caller's
        # inventory session must already have returned its pooled connection.
        assert not active_connections
        refreshed.append(True)

    monkeypatch.setattr(recent_orders, 'refresh', refresh)
    response = client.get('/api/marketplace-sales', headers=headers(signing_keys))
    assert response.status_code == 200
    assert response.json() == {'sales': []}
    assert refreshed == [True]


def test_inventory_derives_status_once_and_reconciles_stale_busy_rows(cloud, signing_keys, monkeypatch):  # noqa: F811
    from vendoo_studio.models.conversation import Conversation
    from vendoo_studio.services import item_status
    from vendoo_studio.services.hosted_database import SellerDatabaseSession

    client, engine = cloud
    alice = headers(signing_keys)
    identity = client.post('/api/conversations', headers=alice, json={
        'title': 'Sold imported tee', 'notes': '{"vendooStatus":"sold"}',
    }).json()['id']
    with SellerDatabaseSession(bind=engine, user_id=USER_ID) as db:
        db.get(Conversation, identity).status = 'in_progress'
        db.commit()
    derive = item_status.statuses
    calls = []

    def counted(db, conversations):
        calls.append(len(conversations))
        return derive(db, conversations)

    monkeypatch.setattr(item_status, 'statuses', counted)
    response = client.get('/api/conversations', headers=alice)
    assert response.status_code == 200
    row = next(row for row in response.json() if row['id'] == identity)
    assert row['status'] == 'sold'
    assert row['missing_sale_data']
    assert calls == [1]
    with SellerDatabaseSession(bind=engine, user_id=USER_ID) as db:
        stored = db.get(Conversation, identity)
        assert stored.status == 'sold'
        assert stored.settled_at is not None


def test_generated_listing_repair_releases_the_connection_before_model_waits(single_connection, monkeypatch):
    """JSON repair and the required-field pass wait on a model; neither may pin the hosted pool."""
    from vendoo_studio.models.conversation import Conversation
    from vendoo_studio.repositories.queries import ConversationRepo
    from vendoo_studio.services import listing_generate, listing_persistence

    waits = []

    def other_session_works(step):
        with single_connection() as other:
            assert other.get(Conversation, 'repair-tee').title == 'Cotton tee'
        waits.append(step)

    async def repair(provider, text):
        other_session_works('repair')
        return {'title': 'Cotton tee', 'price': 20}

    async def fill(provider, listing, **kwargs):
        other_session_works('fill')
        return {**listing, 'price': 25}

    def persist(db, conv_id, full_text, *, parsed, expected_revision_id, **kwargs):
        listing_persistence.save_revision(db, conv_id, parsed, source='model',
                                          expected_revision_id=expected_revision_id)
        return parsed

    monkeypatch.setattr(listing_generate, 'repair_listing_json', repair)
    monkeypatch.setattr(listing_generate, 'fill_validation_gaps', fill)
    monkeypatch.setattr(listing_generate, 'persist_generated_listing', persist)
    monkeypatch.setattr(listing_generate, '_validation_blockers', lambda listing: [] if listing.get('price') == 25 else ['price'])
    with single_connection() as db:
        db.add(Conversation(id='repair-tee', title='Cotton tee'))
        db.commit()
        fingerprint = listing_generate.photo_fingerprint(ConversationRepo(db).get_photos('repair-tee'))
    monkeypatch.setattr('vendoo_studio.database.SessionLocal', single_connection)
    listing = asyncio.run(listing_generate.persist_generated_listing_with_repair(
        'repair-tee', '{"title": "Cotton tee",,', object(), final_announce=False,
        expected_revision_id='', expected_photo_fingerprint=fingerprint))
    assert waits == ['repair', 'fill']
    assert listing['price'] == 25
