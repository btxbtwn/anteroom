import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
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


@pytest.mark.parametrize('kind', ['generate', 'chat'])
def test_submission_releases_connection_before_resolving_seller_provider(
        kind, migration_engine, monkeypatch):  # noqa: F811
    from sqlalchemy import create_engine

    from vendoo_studio.models.conversation import Conversation
    from vendoo_studio.models.user_preference import UserPreference
    from vendoo_studio.routes import hosted_tasks
    from vendoo_studio.services.hosted_context import for_seller
    from vendoo_studio.services.service_context import use

    if migration_engine.dialect.name != 'postgresql':
        pytest.skip('Hosted work requires Postgres')
    schema_migrations.ensure_schema(migration_engine)
    # Even one submission used to deadlock when its ownership read took the
    # last connection and provider selection requested another for settings.
    engine = create_engine(migration_engine.url, pool_size=1, max_overflow=0, pool_timeout=.2)
    monkeypatch.setattr('vendoo_studio.database.engine', engine)
    with Session(migration_engine) as db:
        for owner, primary in [(USER_ID, 'chatgpt'), (BOB, 'mimo')]:
            db.add(Conversation(id=owner, user_id=owner, title='Coat'))
            db.add(Photo(user_id=owner, conversation_id=owner, original_filename='coat.jpg',
                         stored_filename=f'{owner}/{owner}/{"a" * 32}.jpg', size_bytes=1, mime_type='image/jpeg'))
            db.add(UserPreference(user_id=owner, key='settings', value={
                'listing_provider': {'primary': primary, 'fallback': 'none'},
            }))
        db.commit()
    selected = []

    def provider_for(choice):
        from vendoo_studio.services.service_context import current
        selected.append((current().seller_id, choice))
        # API-key providers may read Vault through their own session too.
        with current().session_factory() as db:
            assert db.scalar(select(Conversation.id)) == current().seller_id
        return object()

    monkeypatch.setattr('vendoo_studio.services.hosted_provider.provider_for', provider_for)
    try:
        for owner in (USER_ID, BOB):
            with use(for_seller(owner)) as context, context.session_factory() as db:
                task_id = hosted_tasks.submit(db, owner, kind, 'Please revise the description')
                assert engine.pool.checkedout() == 0
                task = db.get(BackgroundTask, task_id)
                assert (task.user_id, task.conversation_id, task.kind, task.status) == (owner, owner, kind, 'queued')
        # A fresh execution must never reuse another seller's snapshot.
        assert selected == [(USER_ID, 'chatgpt'), (BOB, 'mimo')]
        with use(for_seller(USER_ID)) as context, context.session_factory() as db:
            with pytest.raises(HTTPException) as error:
                hosted_tasks.submit(db, BOB, kind)
            assert error.value.status_code == 404
        assert len(selected) == 2
    finally:
        engine.dispose()


def test_three_drafts_overlap_across_sellers_while_reads_and_cancellation_work(
        migration_engine, jwks, signing_keys, monkeypatch):  # noqa: F811
    if migration_engine.dialect.name != 'postgresql':
        pytest.skip('Hosted work requires Postgres')
    from vendoo_studio.database import SessionLocal
    from vendoo_studio.repositories.queries import ConversationRepo
    from vendoo_studio.services.service_context import current

    schema_migrations.ensure_schema(migration_engine)
    monkeypatch.setattr('vendoo_studio.database.engine', migration_engine)
    monkeypatch.setenv('ANTEROOM_BACKGROUND_WORKER', '1')
    monkeypatch.setattr('vendoo_studio.routes.hosted_tasks.get_listing_provider', lambda: object())
    ready, release = threading.Condition(), threading.Event()
    started = {}

    async def generation(identity, db=None):
        with ready:
            started[identity] = current().seller_id
            ready.notify_all()
        await asyncio.to_thread(release.wait)
        with SessionLocal() as session:
            ConversationRepo(session).add_message(identity, 'assistant', 'Saved ' + identity)

        async def stream():
            yield 'data: Saved draft\n\ndata: [DONE]\n\n'
        return StreamingResponse(stream(), media_type='text/event-stream')

    monkeypatch.setattr('vendoo_studio.routes.chat.generate_listing', generation)
    with TestClient(create_app(PROJECT_URL)) as client:
        owners = [USER_ID, BOB, USER_ID]
        drafts = []
        for index, owner in enumerate(owners):
            response = client.post('/api/conversations', headers=headers(signing_keys, owner),
                                   json={'title': f'Concurrent draft {index}'})
            assert response.status_code == 200
            drafts.append(response.json()['id'])
        with Session(migration_engine) as session:
            for identity, owner in zip(drafts, owners, strict=True):
                session.add(Photo(user_id=owner, conversation_id=identity, original_filename='photo.jpg',
                                  stored_filename=f'{owner}/{identity}/{"a" * 32}.jpg',
                                  size_bytes=1, mime_type='image/jpeg'))
            session.commit()
        with ThreadPoolExecutor(max_workers=3) as executor:
            responses = [executor.submit(client.post, f'/api/conversations/{identity}/generate',
                                         headers=headers(signing_keys, owner))
                         for identity, owner in zip(drafts, owners, strict=True)]
            try:
                with ready:
                    assert ready.wait_for(lambda: len(started) == 3, 5), 'Independent sellers must not wait for another draft'
                assert started == dict(zip(drafts, owners, strict=True))
                assert client.get('/api/health').status_code == 200
                assert client.get(f'/api/conversations/{drafts[1]}', headers=headers(signing_keys)).status_code == 404
                assert client.get(f'/api/conversations/{drafts[2]}', headers=headers(signing_keys)).status_code == 200
                assert client.post(f'/api/conversations/{drafts[0]}/stop', headers=headers(signing_keys)).status_code == 200
            finally:
                release.set()
            assert [response.result(timeout=10).status_code for response in responses] == [200] * 3
        with Session(migration_engine) as session:
            statuses = {task.conversation_id: task.status for task in session.scalars(select(BackgroundTask))}
            assert statuses == {drafts[0]: 'cancelled', drafts[1]: 'completed', drafts[2]: 'completed'}
            assert set(session.execute(select(Message.conversation_id, Message.user_id)).all()) == {
                (drafts[1], BOB), (drafts[2], USER_ID),
            }


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
        async def serve(_engine, stopping=None):
            seen[name] = (threading.get_ident(), asyncio.get_running_loop())
            if stopping is None:
                await asyncio.Event().wait()
            else:
                while not stopping.is_set():
                    await asyncio.sleep(.01)
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


def test_a_task_no_worker_claims_ends_its_stream_and_frees_the_draft(migration_engine, monkeypatch):  # noqa: F811
    from datetime import UTC, datetime, timedelta
    from vendoo_studio.models.conversation import Conversation
    from vendoo_studio.routes import hosted_tasks
    from vendoo_studio.services.hosted_context import for_seller
    from vendoo_studio.services.service_context import use

    if migration_engine.dialect.name != 'postgresql':
        pytest.skip('Hosted work requires Postgres')
    schema_migrations.ensure_schema(migration_engine)
    monkeypatch.setattr('vendoo_studio.database.engine', migration_engine)
    with Session(migration_engine) as session:
        session.add_all([Conversation(id=name, title='Coat', user_id=USER_ID) for name in ('stale', 'fresh')])
        session.commit()
        stale = BackgroundTask(user_id=USER_ID, conversation_id='stale', kind='generate',
                               created_at=datetime.now(UTC) - timedelta(minutes=6))
        fresh = BackgroundTask(user_id=USER_ID, conversation_id='fresh', kind='generate')
        session.add_all([stale, fresh])
        session.commit()
        stale_id, fresh_id = stale.id, fresh.id

    async def follow(task_id, seconds):
        with use(for_seller(USER_ID)):
            frames = []
            async def read():
                async for frame in hosted_tasks.task_stream(task_id).body_iterator:
                    frames.append(frame)
            try:
                await asyncio.wait_for(read(), seconds)
            except TimeoutError:
                pass
            return ''.join(frames)

    ended = asyncio.run(follow(stale_id, 10))
    assert 'event: error\ndata: The draft worker is unavailable; try again shortly.' in ended
    assert ended.endswith('data: [DONE]\n\n')
    waiting = asyncio.run(follow(fresh_id, 1))
    assert 'event: error' not in waiting and '[DONE]' not in waiting
    with Session(migration_engine) as session:
        statuses = {task.id: task.status for task in session.scalars(select(BackgroundTask))}
    # Failed, not left queued: a worker that comes back never runs work the seller was told stopped.
    assert statuses == {stale_id: 'failed', fresh_id: 'queued'}
