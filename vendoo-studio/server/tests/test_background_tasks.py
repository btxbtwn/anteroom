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
