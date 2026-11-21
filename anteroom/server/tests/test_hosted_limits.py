import asyncio
import base64
import io
import threading
import time

import pytest
import sqlalchemy as sa
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from PIL import Image

from test_hosted_drafts import BOB, headers
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import PROJECT_URL, jwks, signing_keys  # noqa: F401
from anteroom.hosted import create_app
from anteroom.services import hosted_context
from anteroom.services.hosted_limits import SellerRateLimit, UploadSlots
from anteroom.services import schema_migrations


def test_one_sellers_slow_upload_does_not_block_another_seller():
    async def run():
        slots = UploadSlots(total=2, wait=0.05)
        async with slots.hold('alice'):
            # Bob is not behind Alice.
            async with slots.hold('bob'):
                pass
            # Alice's second message waits briefly, then is told to retry.
            with pytest.raises(HTTPException) as refused:
                async with slots.hold('alice'):
                    pass
            assert refused.value.status_code == 429
            assert refused.value.headers['Retry-After']
        async with slots.hold('alice'):
            pass
        assert not slots._sellers

    asyncio.run(run())


def test_a_full_machine_answers_quickly_with_a_retryable_503():
    async def run():
        slots = UploadSlots(total=1, wait=0.05)
        async with slots.hold('alice'):
            started = time.monotonic()
            with pytest.raises(HTTPException) as refused:
                async with slots.hold('bob'):
                    pass
            assert time.monotonic() - started < 1
            assert refused.value.status_code == 503
            assert refused.value.headers['Retry-After']
        async with slots.hold('bob'):
            pass
        assert not slots._sellers

    asyncio.run(run())


def test_submissions_are_limited_per_seller_and_refill():
    now = [0.0]
    limit = SellerRateLimit(burst=3, per_second=0.5, clock=lambda: now[0])
    for _ in range(3):
        limit.take('alice')
    with pytest.raises(HTTPException) as refused:
        limit.take('alice')
    assert refused.value.status_code == 429
    assert refused.value.headers['Retry-After'] == '2'
    limit.take('bob')
    now[0] += 2
    limit.take('alice')
    with pytest.raises(HTTPException):
        limit.take('alice')


def test_catalog_states_keep_only_recent_sellers(monkeypatch):
    monkeypatch.setattr(hosted_context, '_CATALOG_STATES', type(hosted_context._CATALOG_STATES)())
    monkeypatch.setattr(hosted_context, 'CATALOG_STATES_KEPT', 3)
    first = hosted_context.catalog_state('a')
    for owner in 'bcd':
        hosted_context.catalog_state(owner)
    assert list(hosted_context._CATALOG_STATES) == ['b', 'c', 'd']
    hosted_context.catalog_state('b')
    hosted_context.catalog_state('e')
    assert list(hosted_context._CATALOG_STATES) == ['d', 'b', 'e']
    assert hosted_context.catalog_state('a') is not first


def test_image_upload_holds_no_draft_lock_and_does_not_block_other_sellers(
        migration_engine, jwks, signing_keys, monkeypatch):  # noqa: F811
    if migration_engine.dialect.name != 'postgresql':
        pytest.skip('Hosted messages require Postgres')
    schema_migrations.ensure_schema(migration_engine)
    monkeypatch.setattr('anteroom.database.engine', migration_engine)
    monkeypatch.setenv('ANTEROOM_BACKGROUND_WORKER', '1')
    monkeypatch.setattr('anteroom.routes.hosted_tasks.get_listing_provider', lambda: object())
    objects = {}

    def storage(method, key, token=None, data=None):
        if method == 'POST':
            objects[key] = data
        elif method == 'DELETE':
            objects.pop(key, None)
        else:
            return objects[key]
        return b''

    monkeypatch.setattr('anteroom.services.hosted_photos.storage_call', storage)
    monkeypatch.setattr('anteroom.services.hosted_storage.photo_call',
                        lambda method, key, data=None: storage(method, key, data=data))

    async def chat(conv_id, body, *, stored_attachments=None):
        async def stream():
            yield 'data: [DONE]\n\n'
        return StreamingResponse(stream(), media_type='text/event-stream')

    monkeypatch.setattr('anteroom.routes.chat.run_message', chat)
    from anteroom.routes import hosted_tasks
    real_store = hosted_tasks.store_uploads
    entered, release, lock_free = threading.Event(), threading.Event(), []

    def slow_store(uploads, *, collection_id=None):
        if collection_id == slow_draft:
            # Another transaction can take the draft's row lock while Storage is slow.
            try:
                with migration_engine.connect() as connection:
                    lock_free.append(connection.scalar(sa.text(
                        'SELECT id FROM conversations WHERE id = :id FOR UPDATE NOWAIT'), {'id': slow_draft}))
                    connection.rollback()
            except Exception as exc:
                lock_free.append(repr(exc)[:300])
            entered.set()
            assert release.wait(10)
        return real_store(uploads, collection_id=collection_id)

    monkeypatch.setattr(hosted_tasks, 'store_uploads', slow_store)
    image = io.BytesIO()
    Image.new('RGB', (8, 6), 'blue').save(image, 'PNG')
    attachment = {'name': 'Tag.png', 'data_url': 'data:image/png;base64,' + base64.b64encode(image.getvalue()).decode()}
    with TestClient(create_app(PROJECT_URL)) as client:
        client.app.state.upload_slots = UploadSlots(total=4, wait=0.2)
        alice, bob = headers(signing_keys), headers(signing_keys, BOB)
        slow_draft = client.post('/api/conversations', headers=alice, json={'title': 'Slow'}).json()['id']
        other_draft = client.post('/api/conversations', headers=alice, json={'title': 'Second'}).json()['id']
        bobs_draft = client.post('/api/conversations', headers=bob, json={'title': 'Bob'}).json()['id']
        result = {}
        slow = threading.Thread(target=lambda: result.setdefault('slow', client.post(
            f'/api/conversations/{slow_draft}/messages', headers=alice, json={'attachments': [attachment]})))
        slow.start()
        try:
            assert entered.wait(10)
            assert lock_free == [slow_draft]
            # Another seller's upload goes through while Alice's is still in Storage.
            response = client.post(f'/api/conversations/{bobs_draft}/messages', headers=bob,
                                   json={'attachments': [attachment]})
            assert response.status_code == 200, response.text
            # Alice's second upload is told to retry instead of hanging.
            busy = client.post(f'/api/conversations/{other_draft}/messages', headers=alice,
                               json={'attachments': [attachment]})
            assert busy.status_code == 429
            assert busy.headers['Retry-After']
        finally:
            release.set()
            slow.join(20)
        assert result['slow'].status_code == 200, result['slow'].text


def test_generation_submissions_are_rate_limited_per_seller(migration_engine, jwks, signing_keys, monkeypatch):  # noqa: F811
    if migration_engine.dialect.name != 'postgresql':
        pytest.skip('Hosted generation requires Postgres')
    schema_migrations.ensure_schema(migration_engine)
    monkeypatch.setattr('anteroom.database.engine', migration_engine)
    monkeypatch.setenv('ANTEROOM_BACKGROUND_WORKER', '1')
    with TestClient(create_app(PROJECT_URL)) as client:
        client.app.state.submission_limit = SellerRateLimit(burst=1, per_second=0.001)
        alice, bob = headers(signing_keys), headers(signing_keys, BOB)
        draft = client.post('/api/conversations', headers=alice, json={'title': 'Draft'}).json()['id']
        bobs = client.post('/api/conversations', headers=bob, json={'title': 'Bob'}).json()['id']
        # No provider: the first is refused for that reason, after using the token.
        assert client.post(f'/api/conversations/{draft}/generate', headers=alice).status_code == 400
        limited = client.post(f'/api/conversations/{draft}/generate', headers=alice)
        assert limited.status_code == 429
        assert limited.headers['Retry-After']
        # Reconnecting to a draft never counts, and Bob has his own allowance.
        assert client.post(f'/api/conversations/{draft}/generate?resume=true', headers=alice).status_code == 200
        assert client.post(f'/api/conversations/{bobs}/generate', headers=bob).status_code == 400


def test_browser_error_reports_are_rate_limited_per_seller(jwks, signing_keys, caplog):  # noqa: F811
    payload = {'kind': 'crash', 'message': 'Boom', 'path': '/inventory'}
    with TestClient(create_app(PROJECT_URL)) as client:
        client.app.state.error_report_limit = SellerRateLimit(burst=2, per_second=0.001)
        alice, bob = headers(signing_keys), headers(signing_keys, BOB)
        with caplog.at_level('ERROR', logger='anteroom.browser'):
            assert client.post('/api/error-reports', headers=alice, json=payload).status_code == 202
            assert client.post('/api/error-reports', headers=alice, json=payload).status_code == 202
            limited = client.post('/api/error-reports', headers=alice, json=payload)
            assert limited.status_code == 429
            assert limited.headers['Retry-After']
            # Another seller keeps an independent allowance.
            assert client.post('/api/error-reports', headers=bob, json=payload).status_code == 202
        # Only the 3 accepted reports were logged.
        assert len(caplog.records) == 3


def test_hosted_vision_and_automation_routes_are_rate_limited_per_seller(jwks, signing_keys, monkeypatch):  # noqa: F811
    monkeypatch.setattr('anteroom.routes.measurements.get_photo_provider', lambda: None)
    image = io.BytesIO()
    Image.new('RGB', (8, 8), 'red').save(image, 'JPEG')
    raw = image.getvalue()

    with TestClient(create_app(PROJECT_URL)) as client:
        alice, bob = headers(signing_keys), headers(signing_keys, BOB)

        # Unsaved measurement & size-tag uploads share the per-seller submission bucket.
        client.app.state.submission_limit = SellerRateLimit(burst=1, per_second=0.001)
        files = [('files', ('a.jpg', raw, 'image/jpeg'))]
        assert client.post('/api/measurements/read', headers=alice, files=files, data={'garment': 'top'}).status_code == 503
        limited = client.post('/api/measurements/read', headers=alice, files=files, data={'garment': 'top'})
        assert limited.status_code == 429
        assert limited.headers['Retry-After']
        assert client.post('/api/size-tags/read', headers=alice, files=files).status_code == 429
        # Bob still has his own token.
        assert client.post('/api/size-tags/read', headers=bob, files=files).status_code == 503

        # Saved-listing measurement & size-tag routes also enforce the per-seller bucket.
        client.app.state.submission_limit = SellerRateLimit(burst=1, per_second=0.001)
        assert client.post('/api/conversations/draft-1/measurements/read', headers=alice, json={'garment': 'top'}).status_code == 503
        limited_saved = client.post('/api/conversations/draft-1/measurements/read', headers=alice, json={'garment': 'top'})
        assert limited_saved.status_code == 429
        assert limited_saved.headers['Retry-After']
        assert client.post('/api/conversations/draft-1/size-tags/read', headers=alice).status_code == 429

        # Automation draft & manual run enforce the per-seller bucket before reaching the handler.
        client.app.state.submission_limit = SellerRateLimit(burst=0, per_second=0.001)
        limited_draft = client.post('/api/automations/draft', headers=alice, json={'instruction': 'Lower prices by 5%'})
        assert limited_draft.status_code == 429
        assert limited_draft.headers['Retry-After']
        limited_run = client.post('/api/automations/auto-1/run', headers=alice)
        assert limited_run.status_code == 429
        assert limited_run.headers['Retry-After']

