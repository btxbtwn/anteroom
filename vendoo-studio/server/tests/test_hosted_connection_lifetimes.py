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
        result = asyncio.run(category_selection.select_categories(db, object(), 'Tee', '', []))
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
    with single_connection() as db:
        asyncio.run(direct_categories.attach_direct_categories(db, {'title': 'Black cotton tee'}))
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
    with single_connection() as db:
        db.add(Conversation(id='draft', title='Tee'))
        db.commit()
        with pytest.raises(HTTPException) as error:
            asyncio.run(chat.generate_listing('draft', db=db))
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
    with single_connection() as db:
        db.add(Conversation(id='draft', title='Tee'))
        db.commit()
        assert asyncio.run(chat.generate_listing('draft', resume=True, db=db)) == (
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
    with single_connection() as db:
        db.add(Conversation(id='draft', title='Tee'))
        db.commit()
        db.add(Photo(conversation_id='draft', original_filename='tee.jpg', stored_filename='tee.jpg',
                     mime_type='image/jpeg', size_bytes=1))
        db.commit()
        assert asyncio.run(chat.generate_listing('draft', db=db)) == 'started'
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
