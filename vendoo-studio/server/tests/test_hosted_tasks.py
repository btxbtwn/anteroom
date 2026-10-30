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
