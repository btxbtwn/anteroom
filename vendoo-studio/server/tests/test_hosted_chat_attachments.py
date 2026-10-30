import asyncio
import base64
import io
import json
from urllib.parse import quote

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_hosted_drafts import BOB, headers
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import PROJECT_URL, USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.hosted import create_app
from vendoo_studio.models.background_task import BackgroundTask
from vendoo_studio.services import schema_migrations


def test_private_images_survive_queue_reload_and_delete_without_leaking_to_another_seller(
        migration_engine, jwks, signing_keys, monkeypatch):  # noqa: F811
    if migration_engine.dialect.name != 'postgresql':
        pytest.skip('Hosted attachments require Postgres')
    schema_migrations.ensure_schema(migration_engine)
    monkeypatch.setattr('vendoo_studio.database.engine', migration_engine)
    monkeypatch.setenv('ANTEROOM_BACKGROUND_WORKER', '1')
    monkeypatch.setattr('vendoo_studio.routes.hosted_tasks.get_listing_provider', lambda: object())
    objects, reads = {}, []

    def storage(method, key, token=None, data=None):
        assert key.startswith(USER_ID + '/')
        if method == 'POST':
            objects[key] = data
        elif method == 'DELETE':
            objects.pop(key, None)
        else:
            reads.append(key)
            return objects[key]
        return b''

    monkeypatch.setattr('vendoo_studio.services.hosted_photos.storage_call', storage)
    monkeypatch.setattr('vendoo_studio.services.hosted_storage.photo_call',
                        lambda method, key, data=None: storage(method, key, data=data))

    async def chat(conv_id, body, db, *, stored_attachments=None):
        from vendoo_studio.repositories.queries import ConversationRepo
        from vendoo_studio.services.chat_attachments import image_parts
        assert len(stored_attachments) == 1
        parts = await asyncio.to_thread(image_parts, stored_attachments)
        assert parts[0]['image_url']['url'].startswith('data:image/jpeg;base64,')
        ConversationRepo(db).add_message(conv_id, 'user', body.text, attachments=stored_attachments)

        async def stream():
            yield 'event: status\ndata: Read the private image\n\n'
            yield 'data: [DONE]\n\n'
        return StreamingResponse(stream(), media_type='text/event-stream')

    monkeypatch.setattr('vendoo_studio.routes.chat.run_message', chat)
    image = io.BytesIO()
    Image.new('RGB', (8, 6), 'blue').save(image, 'PNG')
    encoded = base64.b64encode(image.getvalue()).decode()
    with TestClient(create_app(PROJECT_URL)) as client:
        alice, bob = headers(signing_keys), headers(signing_keys, BOB)
        identity = client.post('/api/conversations', headers=alice, json={'title': 'Image chat'}).json()['id']
        path = '/api/conversations/' + identity
        assert client.post(path + '/messages', headers=bob, content=b'not-json').status_code == 404
        assert client.post(path + '/messages', headers=alice, json={}).status_code == 422
        with monkeypatch.context() as limited:
            limited.setattr('vendoo_studio.routes.hosted_tasks.MAX_MESSAGE_BYTES', 32)
            refused = client.post(path + '/messages', headers=alice, content=b'x' * 33)
            assert refused.status_code == 413
            assert not objects
        forged = client.post(path + '/messages', headers=alice, json={
            'text': 'Use this', 'stored_attachments': [{'id': 'forged-private-reference'}],
        })
        assert forged.status_code == 422
        assert 'forged-private-reference' not in forged.text
        with monkeypatch.context() as cancelled:
            async def disconnected(_request):
                return True
            cancelled.setattr('starlette.requests.Request.is_disconnected', disconnected)
            refused = client.post(path + '/messages', headers=alice, json={
                'attachments': [{'name': 'Tag.png', 'data_url': 'data:image/png;base64,' + encoded}],
            })
            assert refused.status_code == 499
            assert not objects
            with Session(migration_engine) as db:
                assert db.scalar(select(BackgroundTask).where(BackgroundTask.conversation_id == identity)) is None
        with monkeypatch.context() as failed:
            def conflict(*args, **kwargs):
                raise HTTPException(409, 'Queue changed')
            failed.setattr('vendoo_studio.services.background_tasks.enqueue', conflict)
            refused = client.post(path + '/messages', headers=alice, json={
                'attachments': [{'name': 'Tag.png', 'data_url': 'data:image/png;base64,' + encoded}],
            })
            assert refused.status_code == 409
            assert not objects
        response = client.post(path + '/messages', headers=alice, json={
            'attachments': [{'name': 'Tag.png', 'data_url': 'data:image/png;base64,' + encoded}],
        })
        assert response.status_code == 200, response.text
        assert 'Read the private image' in response.text
        messages = client.get(path + '/messages', headers=alice).json()
        attached = messages[0]['attachments'][0]
        assert attached['mime_type'] == 'image/jpeg'
        assert attached['width'] == 8
        key = attached['id']
        assert key.startswith(USER_ID + '/' + identity + '/')
        with Session(migration_engine) as db:
            task = db.scalar(select(BackgroundTask).where(BackgroundTask.conversation_id == identity))
            assert task.status == 'completed'
            payload = json.dumps(task.payload)
            assert encoded not in payload and 'data_url' not in payload
        url = '/api/chat-attachments/' + quote(key, safe='')
        assert client.get(url, headers=alice).content == objects[key]
        assert client.get(url + '?size=96', headers=alice).status_code == 200
        count = len(reads)
        assert client.get(url, headers=bob).status_code == 404
        assert client.get(url).status_code == 401
        assert len(reads) == count
        assert client.delete(path, headers=alice).status_code == 200
        assert not objects
        assert client.get(url, headers=alice).status_code == 404
