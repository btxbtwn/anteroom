import asyncio

import pytest
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, headers
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import PROJECT_URL, USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.hosted import create_app
from vendoo_studio.models.background_task import BackgroundTask, BackgroundTaskEvent
from vendoo_studio.models.conversation import Message, Photo
from vendoo_studio.services import schema_migrations


def test_durable_chat_finishes_preparation_before_done_and_isolates_other_accounts(
        migration_engine, jwks, signing_keys, monkeypatch):  # noqa: F811
    if migration_engine.dialect.name != 'postgresql':
        pytest.skip('Hosted work requires Postgres')
    schema_migrations.ensure_schema(migration_engine)
    monkeypatch.setattr('vendoo_studio.database.engine', migration_engine)
    monkeypatch.setenv('ANTEROOM_BACKGROUND_WORKER', '1')
    monkeypatch.setattr('vendoo_studio.routes.hosted_tasks.get_listing_provider', lambda: object())

    async def fake_generation(conv_id, resume=False, db=None):
        from vendoo_studio.database import SessionLocal
        from vendoo_studio.repositories.queries import ConversationRepo
        from vendoo_studio.services.streaming import spawn

        def save(text):
            with SessionLocal() as session:
                ConversationRepo(session).add_message(conv_id, 'assistant', text)

        async def preparation():
            await asyncio.sleep(.05)
            await asyncio.to_thread(save, 'Finished marketplace field preparation')

        async def stream():
            await asyncio.to_thread(save, 'Saved generated draft')
            yield 'event: status\ndata: Preparing fields\n\n'
            spawn(preparation())
            yield 'data: [DONE]\n\n'
        return StreamingResponse(stream(), media_type='text/event-stream')

    monkeypatch.setattr('vendoo_studio.routes.chat.generate_listing', fake_generation)
    with TestClient(create_app(PROJECT_URL)) as client:
        alice, bob = headers(signing_keys), headers(signing_keys, BOB)
        assert 'generation' in client.get('/api/runtime', headers=alice).json()['features']
        client.app.state.background_worker_enabled = False
        assert 'generation' not in client.get('/api/runtime', headers=alice).json()['features']
        client.app.state.background_worker_enabled = True
        identity = client.post('/api/conversations', headers=alice, json={'title': 'My coat'}).json()['id']
        with Session(migration_engine) as db:
            db.add(Photo(user_id=USER_ID, conversation_id=identity, original_filename='photo.jpg',
                         stored_filename=f'{USER_ID}/{identity}/{"a" * 32}.jpg', size_bytes=1, mime_type='image/jpeg'))
            db.commit()
        path = f'/api/conversations/{identity}'
        client.app.state.background_worker_enabled = False
        assert client.post(path + '/stop', headers=alice).status_code == 200
        assert client.post(path + '/stop', headers=bob).status_code == 404
        client.app.state.background_worker_enabled = True
        assert client.post(path + '/generate', headers=bob).status_code == 404
        assert client.post(path + '/stop', headers=bob).status_code == 404
        response = client.post(path + '/generate', headers=alice)
        assert response.status_code == 200, response.text
        assert 'Preparing fields' in response.text
        assert response.text.count('data: [DONE]') == 1
        with Session(migration_engine) as db:
            task = db.scalar(select(BackgroundTask).where(BackgroundTask.conversation_id == identity))
            assert task.status == 'completed'
            assert task.lease_token is None
            assert db.scalars(select(Message.text).where(Message.conversation_id == identity)).all() == [
                'Saved generated draft', 'Finished marketplace field preparation',
            ]
            events = db.scalars(select(BackgroundTaskEvent).where(BackgroundTaskEvent.task_id == task.id)).all()
            assert len(events) == 1
            assert events[0].user_id == USER_ID
        # A reconnect to completed work must not enqueue another generation.
        assert client.post(path + '/generate?resume=true', headers=alice).text == 'data: [DONE]\n\n'


def test_background_work_runs_off_the_request_event_loop(monkeypatch):
    # Background work uses blocking database sessions. On the request loop, a
    # wait for a pooled connection froze every request, including the ones that
    # would have returned connections, until the pool timed out.
    import threading

    monkeypatch.setenv('ANTEROOM_BACKGROUND_WORKER', '1')
    seen = {}

    def recorder(name):
        async def serve(_engine):
            seen[name] = (threading.get_ident(), asyncio.get_running_loop())
            await asyncio.Event().wait()
        return serve

    monkeypatch.setattr('vendoo_studio.services.background_worker.serve', recorder('drafts'))
    monkeypatch.setattr('vendoo_studio.services.hosted_automation_scheduler.serve', recorder('automations'))
    monkeypatch.setattr('vendoo_studio.services.hosted_offer_scheduler.serve', recorder('offers'))
    monkeypatch.setattr('vendoo_studio.services.hosted_job_watchdog.serve', recorder('watchdog'))
    with TestClient(create_app('https://example.supabase.co')) as client:
        request_thread = client.portal.call(threading.get_ident)
        for _ in range(100):
            if len(seen) == 4:
                break
            threading.Event().wait(.01)
        assert set(seen) == {'drafts', 'automations', 'offers', 'watchdog'}
        assert all(thread != request_thread for thread, _loop in seen.values())
        assert not client.app.state.background_worker.done()
    assert client.app.state.background_worker.done()


def test_a_connection_taken_on_the_request_loop_is_logged(monkeypatch, caplog):
    import logging
    from vendoo_studio import database

    monkeypatch.delenv('ANTEROOM_BACKGROUND_WORKER', raising=False)
    with TestClient(create_app('https://example.supabase.co')) as client, caplog.at_level(logging.WARNING):
        def on_loop():
            with database.engine.connect():
                pass
        client.portal.call(on_loop)
        client.portal.call(on_loop)
    warnings = [record for record in caplog.records if 'request event loop' in record.getMessage()]
    assert len(warnings) == 1
    assert 'test_hosted_tasks.py' not in warnings[0].getMessage()
