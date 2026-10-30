import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import PROJECT_URL, USER_ID, jwks, signing_keys, session_token  # noqa: F401
from vendoo_studio.hosted import create_app
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.services import schema_migrations

BOB = str(uuid.uuid4())


@pytest.fixture
def cloud(migration_engine, jwks, monkeypatch):  # noqa: F811
    if migration_engine.dialect.name != 'postgresql':
        pytest.skip('Hosted data routes require PostgreSQL')
    schema_migrations.ensure_schema(migration_engine)
    monkeypatch.setattr('vendoo_studio.database.engine', migration_engine)
    with TestClient(create_app(PROJECT_URL)) as client:
        yield client, migration_engine


def headers(keys, user=USER_ID):
    return {'Authorization': 'Bearer ' + session_token(keys, sub=user)}


def test_draft_http_flow_is_private_and_rejects_stale_updates(cloud, signing_keys):  # noqa: F811
    client, engine = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    assert client.get('/api/drafts').status_code == 401
    assert client.post('/api/drafts', json={'title':'No sign-in'}).status_code == 401
    created = client.post('/api/drafts', headers=alice, json={'title':'Vintage coat','notes':'Check measurements'})
    assert created.status_code == 201
    draft = created.json()
    assert 'user_id' not in draft
    assert draft['title'] == 'Vintage coat'
    assert client.get('/api/drafts', headers=bob).json() == []
    assert client.get('/api/drafts/'+draft['id'], headers=bob).status_code == 404
    body = {'title':'Edited coat','notes':'Ready for photos','expected_updated_at':draft['updated_at']}
    assert client.patch('/api/drafts/'+draft['id'], headers=bob, json=body).status_code == 404
    updated = client.patch('/api/drafts/'+draft['id'], headers=alice, json=body)
    assert updated.status_code == 200
    assert updated.json()['updated_at'] != draft['updated_at']
    assert client.patch('/api/drafts/'+draft['id'], headers=alice, json=body).status_code == 409
    assert client.get('/api/drafts/'+draft['id'], headers=alice).json()['notes'] == 'Ready for photos'
    assert client.get('/api/drafts', headers=alice).json()[0]['title'] == 'Edited coat'
    assert updated.headers['cache-control'] == 'no-store'
    with Session(engine) as db:
        assert db.get(Conversation, draft['id']).user_id == USER_ID
        assert db.get(Conversation, draft['id']).status == 'draft'


@pytest.mark.parametrize('extra', [{'user_id':BOB}, {'id':'chosen'}, {'status':'published'}, {'box_id':'foreign'}, {'listing_json':{}}])
def test_only_draft_text_can_be_supplied(cloud, signing_keys, extra):  # noqa: F811
    client, _engine = cloud
    response = client.post('/api/drafts', headers=headers(signing_keys), json={'title':'Coat', **extra})
    assert response.status_code == 422
    assert client.get('/api/drafts', headers=headers(signing_keys)).json() == []


def test_non_draft_records_are_not_exposed_or_editable(cloud, signing_keys):  # noqa: F811
    client, engine = cloud
    with Session(engine) as db:
        db.add(Conversation(id='sold-record', user_id=USER_ID, status='sold', title='Sold coat'))
        db.commit()
    assert client.get('/api/drafts', headers=headers(signing_keys)).json() == []
    assert client.get('/api/drafts/sold-record', headers=headers(signing_keys)).status_code == 404


def test_public_web_assets_do_not_expose_data_routes(jwks, tmp_path):  # noqa: F811
    (tmp_path/'assets').mkdir()
    (tmp_path/'assets'/'cloud.js').write_text('console.log("cloud")')
    (tmp_path/'cloud.html').write_text('<!doctype html><title>Anteroom</title>')
    with TestClient(create_app(PROJECT_URL, web_dir=Path(tmp_path))) as client:
        assert client.get('/').status_code == 200
        assert client.head('/').status_code == 405  # GET-only index, no SPA fallback.
        assert client.get('/assets/cloud.js').status_code == 200
        for path in ['/api/drafts','/api/account','/api/conversations','/docs','/cloud.html','/api/health/']:
            assert client.get(path).status_code == 401
        assert client.post('/').status_code == 401
        assert client.get('/assets/../../hosted.py').status_code == 401
        assert "frame-ancestors 'none'" in client.get('/').headers['content-security-policy']


def test_missing_cloud_database_does_not_fall_back_to_local_data(jwks, signing_keys, monkeypatch, tmp_path):  # noqa: F811
    from vendoo_studio.database import make_engine
    local = make_engine(f"sqlite:///{tmp_path / 'local.db'}")
    try:
        monkeypatch.setattr('vendoo_studio.database.engine', local)
        with TestClient(create_app(PROJECT_URL)) as client:
            response = client.get('/api/drafts', headers=headers(signing_keys))
            assert response.status_code == 503
            assert response.json()['detail'] == 'The cloud database is not configured'
    finally:
        local.dispose()
