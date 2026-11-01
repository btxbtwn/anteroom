"""Slow storage and settings reads must not require an extra pooled connection."""
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
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
def test_photo_reads_return_the_database_connection_before_storage(cloud, signing_keys, monkeypatch, route):  # noqa: F811
    client, engine = cloud
    alice = headers(signing_keys)
    draft = client.post('/api/drafts', headers=alice, json={'title': 'Coat'}).json()['id']
    data = image_bytes()

    def storage(method, *_args):
        if method == 'GET':
            assert engine.pool.checkedout() == 0, 'Private image I/O must not retain the request database connection'
        return data

    monkeypatch.setattr(hosted_photos, 'storage_call', storage)
    uploaded = client.post(f'/api/drafts/{draft}/photos', headers=alice, content=data)
    assert uploaded.status_code == 201, uploaded.text
    photo = uploaded.json()['id']
    paths = {'draft': f'/api/drafts/{draft}/photos/{photo}',
             'bytes': f'/api/conversations/{draft}/photos/{photo}/bytes',
             'photo': f'/api/photos/{photo}', 'thumb': f'/api/photos/{photo}/thumb'}
    assert client.get(paths[route], headers=alice).status_code == 200
