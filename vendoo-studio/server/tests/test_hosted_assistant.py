"""The business assistant on the web: one answer at a time per seller, private threads and images."""
import base64
import io
from urllib.parse import quote

import pytest
from PIL import Image

from test_business_assistant import Provider
from test_hosted_drafts import BOB, headers
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.routes import assistant as assistant_route
from vendoo_studio.services import box_scout


@pytest.fixture
def worker_cloud(migration_engine, jwks, monkeypatch):  # noqa: F811
    with _worker_client(migration_engine, monkeypatch) as client:
        yield client


def test_assistant_answers_privately_per_seller_without_reading_local_sourcing(worker_cloud, signing_keys, monkeypatch):  # noqa: F811
    client = worker_cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    objects = {}

    def storage(method, key, token=None, data=None):
        assert key.startswith(USER_ID + '/')
        if method == 'POST':
            objects[key] = data
        elif method == 'DELETE':
            objects.pop(key, None)
        else:
            return objects[key]
        return b''

    monkeypatch.setattr('vendoo_studio.services.hosted_photos.storage_call', storage)
    monkeypatch.setattr('vendoo_studio.services.hosted_storage.photo_call',
                        lambda method, key, data=None: storage(method, key, data=data))
    monkeypatch.setattr(box_scout, 'state_path', lambda: (_ for _ in ()).throw(AssertionError('read the Mac sourcing file')))
    monkeypatch.setattr(assistant_route, 'get_listing_provider', lambda: Provider(('You sold ', 'one item.')))
    runtime = client.get('/api/runtime', headers=alice).json()
    assert 'assistant' in runtime['views'] and 'assistant' in runtime['features']
    assert client.get('/api/assistant/threads').status_code == 401

    assert client.put('/api/assistant/note', headers=alice, json={'note': 'Alice sells coats'}).status_code == 200
    assert client.get('/api/assistant/note', headers=bob).json()['note'] != 'Alice sells coats'
    thread = client.post('/api/assistant/threads', headers=alice).json()['id']
    image = io.BytesIO()
    Image.new('RGB', (8, 6), 'blue').save(image, 'PNG')
    encoded = base64.b64encode(image.getvalue()).decode()
    answer = client.post('/api/assistant/messages', headers=alice, json={
        'text': 'How did I do?', 'thread_id': thread,
        'attachments': [{'name': 'Tag.png', 'data_url': 'data:image/png;base64,' + encoded}]})
    assert answer.status_code == 200, answer.text
    assert 'one item.' in answer.text
    messages = client.get('/api/assistant/messages?thread_id=' + thread, headers=alice).json()
    assert [row['role'] for row in messages] == ['user', 'assistant']
    key = messages[0]['attachments'][0]['id']
    assert key.startswith(f'{USER_ID}/{thread}/') and key in objects
    assert client.get('/api/chat-attachments/' + quote(key), headers=alice).status_code == 200
    assert client.get('/api/chat-attachments/' + quote(key), headers=bob).status_code in (403, 404)

    # Bob never sees Alice's conversation, and his own answer is not blocked by hers.
    assert client.get('/api/assistant/threads', headers=bob).json() == []
    assert client.get('/api/assistant/messages?thread_id=' + thread, headers=bob).status_code == 404
    assert client.post('/api/assistant/messages/resume?thread_id=' + thread, headers=bob).text.strip().endswith('[DONE]')
    assert client.delete('/api/assistant/threads/' + thread, headers=bob).status_code == 404
    assert client.delete('/api/assistant/threads/' + thread, headers=alice).status_code == 200
    assert key not in objects


# On the web an answer is a task in the seller's durable queue, run by the draft worker.

def _worker_client(migration_engine, monkeypatch):  # noqa: F811
    from fastapi.testclient import TestClient
    from test_seller_sessions import PROJECT_URL
    from vendoo_studio.hosted import create_app
    from vendoo_studio.services import schema_migrations

    if migration_engine.dialect.name != 'postgresql':
        pytest.skip('Hosted work requires Postgres')
    schema_migrations.ensure_schema(migration_engine)
    monkeypatch.setattr('vendoo_studio.database.engine', migration_engine)
    monkeypatch.setenv('ANTEROOM_BACKGROUND_WORKER', '1')
    return TestClient(create_app(PROJECT_URL))


def _tasks(engine, thread):
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from vendoo_studio.models.background_task import BackgroundTask

    with Session(engine) as db:
        return [(row.id, row.status, row.error, row.lease_token) for row in db.scalars(
            select(BackgroundTask).where(BackgroundTask.assistant_thread_id == thread)
            .order_by(BackgroundTask.created_at))]


def _seed_cut_off_answer(thread, *, lease_seconds, journal=()):
    """A question whose answer a worker was writing, as if the API stopped (or is still) mid-answer."""
    import uuid
    from datetime import UTC, datetime, timedelta
    from vendoo_studio.database import SessionLocal
    from vendoo_studio.models.background_task import BackgroundTaskEvent
    from vendoo_studio.services import background_tasks, business_assistant
    from vendoo_studio.services.hosted_context import for_seller
    from vendoo_studio.services.service_context import use

    token = str(uuid.uuid4())
    with use(for_seller(USER_ID)), SessionLocal() as db:
        task = background_tasks.add_answer(db, thread, {'question': 'What sold?', 'attachments': []})
        task.status, task.lease_token = 'running', token
        task.leased_until = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        business_assistant.add_message(db, 'user', 'What sold?', thread_id=thread)
        for number, frame in enumerate(journal, 1):
            db.add(BackgroundTaskEvent(task_id=task.id, sequence=number, data=frame))
        db.commit()
        return task.id, token


def test_web_answers_run_in_the_durable_queue_and_complete_with_their_save(migration_engine, jwks, signing_keys, monkeypatch):  # noqa: F811
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from vendoo_studio.models.background_task import BackgroundTaskEvent

    monkeypatch.setattr(assistant_route, 'get_listing_provider', lambda: Provider(('You sold ', 'one item.')))
    with _worker_client(migration_engine, monkeypatch) as client:
        alice, bob = headers(signing_keys), headers(signing_keys, BOB)
        thread = client.post('/api/assistant/threads', headers=alice).json()['id']
        answer = client.post('/api/assistant/messages', headers=alice, json={'text': 'How did I do?', 'thread_id': thread})
        assert answer.status_code == 200, answer.text
        assert 'one item.' in answer.text and answer.text.count('data: [DONE]') == 1
        turns = client.get('/api/assistant/messages?thread_id=' + thread, headers=alice).json()
        assert [(row['role'], row['text']) for row in turns] == [('user', 'How did I do?'), ('assistant', 'You sold one item.')]
        [(task_id, status, error, lease)] = _tasks(migration_engine, thread)
        assert (status, error, lease) == ('completed', None, None)
        with Session(migration_engine) as db:
            frames = db.scalars(select(BackgroundTaskEvent.data).where(BackgroundTaskEvent.task_id == task_id)
                                .order_by(BackgroundTaskEvent.sequence)).all()
        assert assistant_route.journal_text(frames) == 'You sold one item.'
        # Another tab follows the finished answer without starting one; Bob never sees it.
        assert client.post('/api/assistant/messages/resume?thread_id=' + thread, headers=alice).text == 'data: [DONE]\n\n'
        assert client.post('/api/assistant/messages/resume', headers=bob).text == 'data: [DONE]\n\n'
        assert client.post('/api/assistant/messages/retry?thread_id=' + thread, headers=bob).status_code == 404
        assert client.post('/api/assistant/messages/retry?thread_id=' + thread, headers=alice).status_code == 409
        assert client.delete('/api/assistant/threads/' + thread, headers=alice).status_code == 200
        assert _tasks(migration_engine, thread) == []


def test_an_answer_a_restart_cut_off_is_marked_interrupted_and_retried_once(migration_engine, jwks, signing_keys, monkeypatch):  # noqa: F811
    import time

    provider = Provider(('You sold ', 'one item.'))
    monkeypatch.setattr(assistant_route, 'get_listing_provider', lambda: provider)
    with _worker_client(migration_engine, monkeypatch) as client:
        alice = headers(signing_keys)
        thread = client.post('/api/assistant/threads', headers=alice).json()['id']
        # The previous process held the lease when it stopped; it has run out.
        _seed_cut_off_answer(thread, lease_seconds=-1, journal=('data: You so\n\n',))
        for _ in range(100):
            if _tasks(migration_engine, thread)[0][1] != 'running':
                break
            time.sleep(.05)
        [(_task, status, error, _lease)] = _tasks(migration_engine, thread)
        assert status == 'failed' and 'interrupted' in error and 'Nothing from it was saved' in error
        assert provider.calls == []  # never replayed on its own
        assert [row['role'] for row in client.get('/api/assistant/messages?thread_id=' + thread, headers=alice).json()] == ['user']
        resumed = client.post('/api/assistant/messages/resume?thread_id=' + thread, headers=alice).text
        assert 'event: error\ndata: This answer was interrupted' in resumed and 'You so' not in resumed

        retried = client.post('/api/assistant/messages/retry?thread_id=' + thread, headers=alice)
        assert retried.status_code == 200 and 'one item.' in retried.text
        assert len(provider.calls) == 1 and provider.seen[-1] == {'role': 'user', 'content': 'What sold?'}
        turns = client.get('/api/assistant/messages?thread_id=' + thread, headers=alice).json()
        assert [(row['role'], row['text']) for row in turns] == [('user', 'What sold?'), ('assistant', 'You sold one item.')]
        assert [row[1] for row in _tasks(migration_engine, thread)] == ['failed', 'completed']
        assert client.post('/api/assistant/messages/retry?thread_id=' + thread, headers=alice).status_code == 409


def test_stop_keeps_what_was_written_and_fences_the_workers_late_save(migration_engine, jwks, signing_keys, monkeypatch):  # noqa: F811
    from sqlalchemy.exc import IntegrityError
    from vendoo_studio.database import SessionLocal
    from vendoo_studio.services import background_tasks
    from vendoo_studio.services.hosted_context import for_seller
    from vendoo_studio.services.service_context import use

    with _worker_client(migration_engine, monkeypatch) as client:
        alice = headers(signing_keys)
        thread = client.post('/api/assistant/threads', headers=alice).json()['id']
        # A live answer, still leased by its worker.
        task_id, token = _seed_cut_off_answer(thread, lease_seconds=60, journal=(
            'event: status\ndata: Reading your shop…\n\n', 'data: You sold\n\n', 'data:  two\ndata: things\n\n'))
        # One answer at a time per seller, even from another tab or Machine.
        with use(for_seller(USER_ID)), SessionLocal() as db:
            background_tasks.add_answer(db, thread, {'question': 'Again?'})
            with pytest.raises(IntegrityError):
                db.commit()
        assert client.post('/api/assistant/messages', headers=alice,
                           json={'text': 'Again?', 'thread_id': thread}).status_code == 409
        assert client.delete('/api/assistant/threads/' + thread, headers=alice).status_code == 409

        assert client.post('/api/assistant/messages/stop', headers=alice).json() == {'ok': True}
        expected = [('user', 'What sold?'), ('assistant', 'You sold two\nthings')]
        turns = client.get('/api/assistant/messages?thread_id=' + thread, headers=alice).json()
        assert [(row['role'], row['text']) for row in turns] == expected
        assert _tasks(migration_engine, thread)[0][1] == 'cancelled'

        # The worker that was writing it finishes late: its save is refused, so nothing is kept twice.
        with use(for_seller(USER_ID, task_lease=(task_id, token))):
            with pytest.raises(background_tasks.TaskLeaseExpired):
                assistant_route._save_answer(
                    Provider(), 'You sold two things.', None, None, None, thread,
                    lambda db: background_tasks.complete_in(db, task_id, token))
        turns = client.get('/api/assistant/messages?thread_id=' + thread, headers=alice).json()
        assert [(row['role'], row['text']) for row in turns] == expected


def test_a_queued_answer_runs_without_holding_a_database_connection(migration_engine, jwks, signing_keys, monkeypatch):  # noqa: F811
    """The answer waits minutes on a model; the claimed task row must not keep a pooled connection meanwhile."""
    import asyncio
    from sqlalchemy import event
    from vendoo_studio.services import background_tasks, background_worker
    from vendoo_studio.services.hosted_context import for_seller
    from vendoo_studio.services.service_context import use

    with _worker_client(migration_engine, monkeypatch) as client:
        thread = client.post('/api/assistant/threads', headers=headers(signing_keys)).json()['id']
    task_id, token = _seed_cut_off_answer(thread, lease_seconds=60)
    active, seen = set(), []

    def checkout(connection, *_args):
        active.add(connection)

    def checkin(connection, *_args):
        active.discard(connection)

    async def handler(_task_id, _token, task):
        fields = (task.payload['question'], task.assistant_thread_id)
        seen.append((fields, len(active)))
        return True

    monkeypatch.setitem(background_worker._handlers, background_tasks.ASSISTANT, handler)
    event.listen(migration_engine, 'checkout', checkout)
    event.listen(migration_engine, 'checkin', checkin)
    try:
        with use(for_seller(USER_ID, task_lease=(task_id, token))):
            assert asyncio.run(background_worker._execute(task_id, token)) is True
    finally:
        event.remove(migration_engine, 'checkout', checkout)
        event.remove(migration_engine, 'checkin', checkin)
    assert seen == [(('What sold?', thread), 0)], 'The answer must not run with a database connection checked out'
