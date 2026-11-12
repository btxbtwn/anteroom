from datetime import UTC, datetime, timedelta
from dataclasses import replace
import uuid

from fastapi import HTTPException
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from test_hosted_service_storage import storage  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_service_context import runtime
from vendoo_studio.models.background_task import BackgroundTask
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.services import background_tasks as queue, service_context


def prepare(storage_fixture):
    engine, sessions = storage_fixture
    owner, other = str(uuid.uuid4()), str(uuid.uuid4())
    with sessions(owner)() as db:
        conversation = Conversation(title='My draft')
        db.add(conversation)
        db.commit()
        identity = conversation.id
        task = queue.enqueue(db, identity, 'generate', {'expected_revision_id': ''})
        task_id = task.id
    return engine, sessions, owner, other, identity, task_id


def test_queue_claim_is_private_and_submissions_remain_seller_scoped(storage):  # noqa: F811
    engine, sessions, owner, other, identity, task_id = prepare(storage)
    with sessions(other)() as db:
        assert db.get(BackgroundTask, task_id) is None
        with pytest.raises(HTTPException) as error:
            queue.enqueue(db, identity, 'generate', {})
        assert error.value.status_code == 404
    with sessions(owner)() as db:
        with pytest.raises(HTTPException) as busy:
            queue.require_idle(db, identity)
        assert busy.value.status_code == 409
        assert busy.value.headers == {'X-Anteroom-Reason': 'draft_busy'}
        with pytest.raises(HTTPException) as error:
            queue.enqueue(db, identity, 'chat', {})
        assert error.value.status_code == 409
        with pytest.raises(ValueError, match='Only draft'):
            queue.enqueue(db, identity, 'publish', {})
    with sessions(owner)() as db, pytest.raises(DBAPIError):
        db.execute(text('SELECT * FROM anteroom_private.claim_background_task(:token)'),
                   {'token': str(uuid.uuid4())}).all()
    claimed, claimed_owner, token = queue.claim(engine)
    assert (claimed, claimed_owner) == (task_id, owner)
    assert queue.claim(engine) is None
    context = replace(runtime(owner), session_factory=sessions(owner))
    with service_context.use(context):
        queue.renew(task_id, token)
        with pytest.raises(queue.TaskLeaseExpired):
            queue.finish(task_id, 'wrong-worker')
        queue.finish(task_id, token)
    with sessions(owner)() as db:
        assert db.get(BackgroundTask, task_id).status == 'completed'


def test_cancelled_worker_cannot_write_and_expired_work_is_never_replayed(storage):  # noqa: F811
    engine, sessions, owner, _other, identity, task_id = prepare(storage)
    _, _, token = queue.claim(engine)
    with sessions(owner)() as db:
        queue.cancel(db, task_id)
    with sessions(owner)() as db:
        db.info['background_lease'] = (task_id, token)
        row = db.get(Conversation, identity)
        row.title = 'Late write from cancelled worker'
        with pytest.raises(queue.TaskLeaseExpired):
            db.commit()
    with sessions(owner)() as db:
        assert db.get(Conversation, identity).title == 'My draft'
        task_id = queue.enqueue(db, identity, 'generate', {}).id
    _, _, token = queue.claim(engine)
    with engine.begin() as connection:
        connection.execute(update(BackgroundTask).where(BackgroundTask.id == task_id)
                           .values(leased_until=datetime.now(UTC) - timedelta(seconds=1)))
    assert queue.claim(engine) is None
    with sessions(owner)() as db:
        row = db.get(BackgroundTask, task_id)
        assert row.status == 'failed'
        assert 'Review the saved draft' in row.error
        assert db.scalar(select(Conversation.title).where(Conversation.id == identity)) == 'My draft'


def test_a_stopped_task_is_told_apart_from_a_lost_lease(storage):  # noqa: F811
    engine, sessions, owner, _other, identity, task_id = prepare(storage)
    _, _, token = queue.claim(engine)
    with sessions(owner)() as db:
        queue.cancel(db, task_id)
    with service_context.use(replace(runtime(owner), session_factory=sessions(owner))):
        with pytest.raises(queue.TaskStopped):
            queue.renew(task_id, token)
        with pytest.raises(queue.TaskStopped):
            queue.append_events(task_id, token, ['data: late\n\n'])
        with sessions(owner)() as db:
            task_id = queue.enqueue(db, identity, 'generate', {}).id
        _, _, token = queue.claim(engine)
        with engine.begin() as connection:
            connection.execute(update(BackgroundTask).where(BackgroundTask.id == task_id)
                               .values(leased_until=datetime.now(UTC) - timedelta(seconds=1)))
        with pytest.raises(queue.TaskLeaseExpired) as lost:
            queue.renew(task_id, token)
        assert not isinstance(lost.value, queue.TaskStopped)


@pytest.mark.parametrize('outcome', ['completed', 'failed', 'cancelled'])
def test_lease_renewal_does_not_refresh_every_open_editor(storage, outcome):  # noqa: F811
    from vendoo_studio.models.seller_change import SellerChange

    engine, sessions, owner, _other, identity, task_id = prepare(storage)
    _, _, token = queue.claim(engine)
    with sessions(owner)() as db:
        announced = db.scalar(select(SellerChange.changed_at).where(SellerChange.topic == 'listings'))
        leased_until = db.get(BackgroundTask, task_id).leased_until
    with service_context.use(replace(runtime(owner), session_factory=sessions(owner))):
        queue.renew(task_id, token)
        with sessions(owner)() as db:
            assert db.get(BackgroundTask, task_id).leased_until > leased_until
            assert db.scalar(select(SellerChange.changed_at).where(SellerChange.topic == 'listings')) == announced
        if outcome == 'cancelled':
            with sessions(owner)() as db:
                queue.cancel(db, task_id)
        else:
            queue.finish(task_id, token, error='Interrupted' if outcome == 'failed' else None)
    with sessions(owner)() as db:
        assert db.get(BackgroundTask, task_id).status == outcome
        assert db.scalar(select(SellerChange.changed_at).where(SellerChange.topic == 'listings')) > announced


def test_assistant_task_finishes_without_a_listing(storage):  # noqa: F811
    from vendoo_studio.models.assistant import AssistantThread

    engine, sessions = storage
    owner = str(uuid.uuid4())
    with sessions(owner)() as db:
        thread = AssistantThread()
        db.add(thread)
        db.flush()
        task = queue.add_answer(db, thread.id, {'question': 'What sold?'})
        db.commit()
        task_id = task.id
    assert queue.claim(engine)[:2] == (task_id, owner)
    with sessions(owner)() as db:
        token = db.get(BackgroundTask, task_id).lease_token
    with service_context.use(replace(runtime(owner), session_factory=sessions(owner))):
        queue.finish(task_id, token, error='Interrupted')
    with sessions(owner)() as db:
        assert db.get(BackgroundTask, task_id).status == 'failed'


@pytest.mark.parametrize('leased_session', [True, False])
def test_progress_events_check_the_lease_once_per_write(storage, leased_session):  # noqa: F811
    """Each generating draft writes progress a few times a second; every extra
    round trip there keeps a pooled connection away from the seller's pages."""
    from sqlalchemy import event

    engine, sessions, owner, _other, _identity, task_id = prepare(storage)
    _, _, token = queue.claim(engine)

    def worker_sessions():
        # The worker's sessions carry its lease, as hosted_context.for_seller sets it.
        session = sessions(owner)()
        if leased_session:
            session.info['background_lease'] = (task_id, token)
        return session

    lease_checks, statements = [], []

    def count(_conn, _cursor, statement, *_args):
        # The isolation check runs once per pooled connection, not per write (NullPool here).
        if 'rolsuper' not in statement and 'pg_policy' not in statement:
            statements.append(statement)
        if 'FROM background_tasks' in statement and 'lease_token' in statement:
            lease_checks.append(statement)

    event.listen(engine, 'before_cursor_execute', count)
    try:
        with service_context.use(replace(runtime(owner), session_factory=worker_sessions)):
            queue.append_events(task_id, token, ['data: one\n\n', 'data: two\n\n'])
            queue.append_events(task_id, token, ['data: three\n\n'])
    finally:
        event.remove(engine, 'before_cursor_execute', count)
    assert len(lease_checks) == 2
    # Per write: the seller scope, the lease check, and one insert that numbers its own events.
    assert len(statements) == 6, "\n".join(s[:90] for s in statements)
    with sessions(owner)() as db:
        assert queue.journal(db, task_id) == ['data: one\n\n', 'data: two\n\n', 'data: three\n\n']
        queue.cancel(db, task_id)
    with service_context.use(replace(runtime(owner), session_factory=worker_sessions)):
        with pytest.raises(queue.TaskLeaseExpired):
            queue.append_events(task_id, token, ['data: late\n\n'])
    with sessions(owner)() as db:
        assert queue.journal(db, task_id)[-1] == 'data: three\n\n'
