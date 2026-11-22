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
from anteroom.routes import assistant as assistant_route
from anteroom.services import box_scout


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

    monkeypatch.setattr('anteroom.services.hosted_photos.storage_call', storage)
    monkeypatch.setattr('anteroom.services.hosted_storage.photo_call',
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
    from anteroom.hosted import create_app
    from anteroom.services import schema_migrations

    if migration_engine.dialect.name != 'postgresql':
        pytest.skip('Hosted work requires Postgres')
    schema_migrations.ensure_schema(migration_engine)
    monkeypatch.setattr('anteroom.database.engine', migration_engine)
    monkeypatch.setenv('ANTEROOM_BACKGROUND_WORKER', '1')
    return TestClient(create_app(PROJECT_URL))


def _tasks(engine, thread):
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from anteroom.models.background_task import BackgroundTask

    with Session(engine) as db:
        return [(row.id, row.status, row.error, row.lease_token) for row in db.scalars(
            select(BackgroundTask).where(BackgroundTask.assistant_thread_id == thread)
            .order_by(BackgroundTask.created_at))]


def _seed_cut_off_answer(thread, *, lease_seconds, journal=()):
    """A question whose answer a worker was writing, as if the API stopped (or is still) mid-answer."""
    import uuid
    from datetime import UTC, datetime, timedelta
    from anteroom.database import SessionLocal
    from anteroom.models.background_task import BackgroundTaskEvent
    from anteroom.services import background_tasks, business_assistant
    from anteroom.services.hosted_context import for_seller
    from anteroom.services.service_context import use

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
    from anteroom.models.background_task import BackgroundTaskEvent

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


@pytest.mark.parametrize('failure,partial', [
    ('provider', ''), ('empty', ''), ('lookups', ''), ('provider', 'Half an answer.'),
    ('lookups', 'Half an answer.'), ('stalled', ''), ('prepare', ''),
])
def test_failed_answers_keep_a_safe_reason_on_resume_and_retry_without_duplicate_questions(
    migration_engine, jwks, signing_keys, monkeypatch, caplog, failure, partial,  # noqa: F811
):  # noqa: F811
    from anteroom.services import business_assistant
    from anteroom.services.provider_deadlines import ProviderStalled

    secret = 'private-provider-response-and-shop-data'
    reason = {
        'provider': 'ChatGPT could not finish the assistant answer. Try again.',
        'empty': 'The assistant returned an empty answer. Ask again.',
        'lookups': 'The assistant kept looking things up without answering. Ask again, or ask something narrower.',
        'stalled': 'ChatGPT stopped responding while writing its answer: nothing came back for 5 minutes.',
        'prepare': 'Anteroom could not read your shop for this answer. Try again.',
    }[failure]

    async def answer(_provider, _messages, _shop, timing=None):
        if partial:
            yield 'text', partial
        if failure == 'provider':
            raise RuntimeError(secret)
        if failure == 'stalled':
            raise ProviderStalled(reason)
        if failure == 'lookups':
            yield 'error', reason

    monkeypatch.setattr(assistant_route, 'get_listing_provider', lambda: Provider())
    monkeypatch.setattr(business_assistant, 'answer', answer)
    prepare = assistant_route._prepare
    if failure == 'prepare':
        from sqlalchemy.exc import InternalError

        class IdleTransaction(Exception):
            sqlstate = '25P03'

        def broken_prepare(*_args):
            raise InternalError('SELECT private_shop_data', {'secret': secret}, IdleTransaction(secret))
        monkeypatch.setattr(assistant_route, '_prepare', broken_prepare)
    with _worker_client(migration_engine, monkeypatch) as client:
        alice, bob = headers(signing_keys), headers(signing_keys, BOB)
        thread = client.post('/api/assistant/threads', headers=alice).json()['id']
        response = client.post('/api/assistant/messages', headers=alice,
                               json={'text': 'What should I buy today?', 'thread_id': thread})
        assert response.status_code == 200 and f'event: error\ndata: {reason}' in response.text
        if failure == 'lookups':
            assert 'returned an empty answer' not in response.text
        [(_task, status, error, lease)] = _tasks(migration_engine, thread)
        assert (status, error, lease) == ('failed', reason, None)
        resumed = client.post('/api/assistant/messages/resume?thread_id=' + thread, headers=alice).text
        assert resumed == f'event: error\ndata: {reason}\n\ndata: [DONE]\n\n'
        assert client.post('/api/assistant/messages/resume?thread_id=' + thread, headers=bob).text == 'data: [DONE]\n\n'
        turns = client.get('/api/assistant/messages?thread_id=' + thread, headers=alice).json()
        assert [row['text'] for row in turns] == ['What should I buy today?'] + ([partial] if partial else [])
        assert secret not in response.text + resumed + caplog.text
        assert 'SELECT private_shop_data' not in response.text + resumed + caplog.text
        if failure == 'prepare':
            assert 'SQLSTATE 25P03' in caplog.text
        assert all(record.exc_info is None for record in caplog.records if record.name == 'anteroom.assistant')
        if partial:
            # Existing semantics: a saved partial answer is not silently replaced by Retry.
            assert client.post('/api/assistant/messages/retry?thread_id=' + thread, headers=alice).status_code == 409
        else:
            monkeypatch.setattr(assistant_route, '_prepare', prepare)
            async def recovered(_provider, _messages, _shop, timing=None):
                yield 'text', 'Buy coats.'
            monkeypatch.setattr(business_assistant, 'answer', recovered)
            assert 'Buy coats.' in client.post('/api/assistant/messages/retry?thread_id=' + thread, headers=alice).text
            turns = client.get('/api/assistant/messages?thread_id=' + thread, headers=alice).json()
            assert [row['text'] for row in turns] == ['What should I buy today?', 'Buy coats.']
            assert [row[1] for row in _tasks(migration_engine, thread)] == ['failed', 'completed']
            assert client.post('/api/assistant/messages/resume?thread_id=' + thread, headers=alice).text == 'data: [DONE]\n\n'


def test_shop_snapshot_survives_calculation_longer_than_the_idle_transaction_deadline(
    migration_engine, jwks, monkeypatch,  # noqa: F811
):
    import time
    from test_business_assistant import NOW, add_listing
    from anteroom.database import SessionLocal
    from anteroom.services import business_assistant, hosted_database, size_audit
    from anteroom.services.hosted_context import for_seller
    from anteroom.services.service_context import use

    with _worker_client(migration_engine, monkeypatch):
        with use(for_seller(USER_ID)), SessionLocal() as db:
            add_listing(db, 'Coat M', {'title': 'Coat M', 'size': 'M', 'price': 40})
            monkeypatch.setattr(hosted_database, 'IDLE_IN_TRANSACTION_TIMEOUT', '150ms')
            original = size_audit.size_problems
            audited = []

            def slow_audit(listing, photo):
                # Stand in for a large shop's size calculations; the next history
                # read must use a fresh transaction, not a server-killed connection.
                time.sleep(.25)
                audited.append(listing['title'])
                return original(listing, photo)

            monkeypatch.setattr(size_audit, 'size_problems', slow_audit)
            shop = business_assistant.read_shop(db, now=NOW)
            assert audited == ['Coat M']
            assert 'Coat M' in business_assistant.run_lookup(shop.lookups, 'SELECT title FROM listings')
            shop.lookups.close()


def test_shop_snapshot_waits_for_the_sales_archive_lock_outside_a_transaction(
    migration_engine, jwks, monkeypatch,  # noqa: F811
):
    """Another thread holding the imported-sales archive lock must not cost the snapshot its connection.

    ``ensure_archived`` takes one process-wide lock before every sales read.
    While another seller's first archive holds it, the snapshot waits; Postgres
    ends a transaction left idle that long. Each wait here is a real one: a
    second thread takes the lock and keeps it for 300 ms, twice the deadline.
    """
    import threading
    import time
    from test_business_assistant import NOW, add_listing
    from anteroom.database import SessionLocal
    from anteroom.services import business_assistant, hosted_database, scout, vendoo_sales_archive
    from anteroom.services.hosted_context import for_seller
    from anteroom.services.service_context import use

    lock = threading.Lock()
    waits = []

    class HeldByAnotherThread:
        def __enter__(self):
            taken, timed, done = threading.Event(), threading.Event(), threading.Event()

            def hold():
                with lock:
                    taken.set()
                    timed.wait()
                    time.sleep(.3)
                done.set()

            threading.Thread(target=hold, daemon=True).start()
            assert taken.wait(5)
            started = time.monotonic()
            timed.set()
            lock.acquire()
            waits.append(time.monotonic() - started)
            done.wait(5)
            return self

        def __exit__(self, *_exc):
            lock.release()

    with _worker_client(migration_engine, monkeypatch):
        with use(for_seller(USER_ID)), SessionLocal() as db:
            add_listing(db, 'Coat M', {'title': 'Coat M', 'size': 'M', 'price': 40})
            db.commit()
            monkeypatch.setattr(hosted_database, 'IDLE_IN_TRANSACTION_TIMEOUT', '150ms')
            monkeypatch.setattr(vendoo_sales_archive, '_archive_lock', HeldByAnotherThread())
            outcomes = scout.outcomes

            def read_scout(session, rows):
                # The preceding sales-event read must not leave an idle transaction
                # for Postgres to kill if work is delayed before Scout starts.
                assert not session.in_transaction()
                return outcomes(session, rows)

            monkeypatch.setattr(scout, 'outcomes', read_scout)
            recent = scout.recent

            def delayed_recent(session):
                # A pause before the next Scout query must not leave the
                # track-record transaction idle past the same deadline.
                time.sleep(.25)
                return recent(session)

            monkeypatch.setattr(scout, 'recent', delayed_recent)
            shop = business_assistant.read_shop(db, now=NOW)
            assert 'Coat M' in business_assistant.run_lookup(shop.lookups, 'SELECT title FROM listings')
            shop.lookups.close()
    # The listings read and the sales read each waited behind the other thread.
    assert len(waits) >= 2 and min(waits) >= .2


def test_four_snapshots_for_two_sellers_release_connections_during_other_reads_and_writes(
    migration_engine, jwks, signing_keys, monkeypatch,  # noqa: F811
):
    import threading
    import time
    from collections import defaultdict
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import event
    from test_business_assistant import NOW, add_listing
    from anteroom.database import SessionLocal
    from anteroom.services import business_assistant, size_audit
    from anteroom.services.hosted_context import for_seller
    from anteroom.services.service_context import use

    active, guard = defaultdict(int), threading.Lock()
    ready, release = threading.Barrier(5), threading.Event()

    def checkout(*_args):
        with guard:
            active[threading.get_ident()] += 1

    def checkin(*_args):
        with guard:
            active[threading.get_ident()] -= 1

    def audit(_listing, _photo):
        with guard:
            assert active[threading.get_ident()] == 0, 'Snapshot calculations held a pooled connection'
        ready.wait(timeout=15)
        assert release.wait(timeout=15)

    def snapshot(owner):
        with use(for_seller(owner)), SessionLocal() as db:
            shop = business_assistant.read_shop(db, now=NOW)
            try:
                return business_assistant.run_lookup(shop.lookups, 'SELECT title FROM listings')
            finally:
                shop.lookups.close()

    with _worker_client(migration_engine, monkeypatch) as client:
        for owner in (USER_ID, BOB):
            with use(for_seller(owner)), SessionLocal() as db:
                add_listing(db, owner + ' Coat M', {'title': owner + ' Coat M', 'size': 'M', 'price': 40})
        monkeypatch.setattr(size_audit, 'size_problems', audit)
        event.listen(migration_engine, 'checkout', checkout)
        event.listen(migration_engine, 'checkin', checkin)
        times = []
        try:
            with ThreadPoolExecutor(max_workers=4) as workers:
                owners = [USER_ID, USER_ID, BOB, BOB]
                futures = [workers.submit(snapshot, owner) for owner in owners]
                try:
                    ready.wait(timeout=15)
                    for owner in (USER_ID, BOB) * 5:
                        for method in ('get', 'post'):
                            started = time.monotonic()
                            response = getattr(client, method)('/api/assistant/threads', headers=headers(signing_keys, owner))
                            times.append((time.monotonic() - started) * 1000)
                            assert response.status_code == 200
                finally:
                    release.set()
                for owner, future in zip(owners, futures, strict=True):
                    result = future.result()
                    assert owner in result and (BOB if owner == USER_ID else USER_ID) not in result
        finally:
            event.remove(migration_engine, 'checkout', checkout)
            event.remove(migration_engine, 'checkin', checkin)
        times.sort()
        print(f'Four snapshots / two sellers; {len(times)} reads and writes: '
              f'p50={times[len(times) // 2]:.1f}ms p95={times[int(len(times) * .95) - 1]:.1f}ms; errors=0')


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
    from anteroom.database import SessionLocal
    from anteroom.services import background_tasks
    from anteroom.services.hosted_context import for_seller
    from anteroom.services.service_context import use

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
                    lambda db, error: background_tasks.complete_in(db, task_id, token, error=error))
        turns = client.get('/api/assistant/messages?thread_id=' + thread, headers=alice).json()
        assert [(row['role'], row['text']) for row in turns] == expected


def test_a_queued_answer_runs_without_holding_a_database_connection(migration_engine, jwks, signing_keys, monkeypatch):  # noqa: F811
    """The answer waits minutes on a model; the claimed task row must not keep a pooled connection meanwhile."""
    import asyncio
    from sqlalchemy import event
    from anteroom.services import background_tasks, background_worker
    from anteroom.services.hosted_context import for_seller
    from anteroom.services.service_context import use

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


def test_proposal_row_lock_rejects_another_process(migration_engine, monkeypatch):  # noqa: F811
    """The database guard must work independently of the Mac's process lock."""
    from fastapi import HTTPException
    from sqlalchemy.orm import Session
    from anteroom.models.assistant import AssistantMessage, AssistantThread
    from anteroom.services import schema_migrations

    engine = migration_engine
    if engine.dialect.name != 'postgresql':
        pytest.skip('Cross-process row locks require Postgres')
    schema_migrations.ensure_schema(engine)
    with Session(engine) as db:
        thread = AssistantThread(title='Concurrent proposal')
        db.add(thread)
        db.flush()
        message = AssistantMessage(thread_id=thread.id, role='assistant', text='Change',
                                   changes={'status': 'proposed', 'items': [{'listing_id': 'item'}]})
        db.add(message)
        db.commit()
        message_id = message.id

    def apply(item, db):
        with Session(engine) as other:
            with pytest.raises(HTTPException) as refused:
                assistant_route._proposal(message_id, other)
            assert refused.value.status_code == 409
        return None

    monkeypatch.setattr(assistant_route, '_apply_to_listing', apply)
    with Session(engine) as db:
        result = assistant_route.apply_changes(message_id, db)
        assert result['changes']['status'] == 'applied'
