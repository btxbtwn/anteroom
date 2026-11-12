"""Open draft and assistant streams read Postgres only when their task has something new."""
import asyncio
import time
import uuid
from dataclasses import replace

import pytest
from fastapi import HTTPException
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from test_seller_ownership import migration_engine  # noqa: F401
from test_service_context import runtime
from vendoo_studio.models.background_task import BackgroundTask
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.services import background_tasks as queue, schema_migrations, service_context, task_signals
from vendoo_studio.services.hosted_database import SellerDatabaseSession

ALICE, BOB = str(uuid.uuid4()), str(uuid.uuid4())


@pytest.fixture
def hosted(migration_engine, monkeypatch):  # noqa: F811
    if migration_engine.dialect.name != 'postgresql':
        pytest.skip('Task notices need Postgres')
    schema_migrations.ensure_schema(migration_engine)
    monkeypatch.setattr('vendoo_studio.database.engine', migration_engine)
    yield migration_engine
    task_signals.stop()


def running_task(engine, owner):
    """A claimed generation for ``owner``; returns its id and the worker's lease token."""
    with Session(engine) as session:
        conversation = Conversation(title='Coat', user_id=owner)
        session.add(conversation)
        session.flush()
        session.add(BackgroundTask(user_id=owner, conversation_id=conversation.id, kind='generate'))
        session.commit()
    task_id, claimed_owner, token = queue.claim(engine)
    assert claimed_owner == owner
    return task_id, token


def write(engine, owner, task_id, token, frame):
    """What the worker does: append progress in its own seller session."""
    def sessions():
        return SellerDatabaseSession(user_id=owner, bind=engine)
    with service_context.use(replace(runtime(owner), session_factory=sessions)):
        queue.append_events(task_id, token, [frame])


class Checkouts:
    def __init__(self, engine):
        self.engine, self.count = engine, 0
        event.listen(engine, 'checkout', self.checkout)

    def checkout(self, *_args):
        self.count += 1

    def close(self):
        event.remove(self.engine, 'checkout', self.checkout)


async def wait_until(condition, seconds=10.0):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, 'timed out'
        await asyncio.sleep(.02)


class Follower:
    """Reads one task's stream as the given seller, keeping every frame."""

    def __init__(self, owner, task_id):
        from vendoo_studio.routes.hosted_tasks import task_stream
        from vendoo_studio.services.hosted_context import for_seller

        self.frames: list[str] = []
        self.error: BaseException | None = None

        async def read():
            try:
                with service_context.use(for_seller(owner)):
                    response = task_stream(task_id)
                    async for frame in response.body_iterator:
                        self.frames.append(frame)
            except HTTPException as exc:
                self.error = exc
        self.task = asyncio.create_task(read())

    def text(self):
        return ''.join(self.frames)

    async def close(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)


def test_an_idle_stream_does_not_read_and_wakes_on_new_progress(hosted, monkeypatch):
    # The fallback is far away, so only a notice can explain a prompt wake-up.
    monkeypatch.setattr(task_signals, 'FALLBACK_S', 30.0)
    task_id, token = running_task(hosted, ALICE)
    signals = task_signals.start(hosted.url)

    async def scenario():
        await wait_until(lambda: signals.connected)
        follower = Follower(ALICE, task_id)
        await wait_until(lambda: ': keepalive\n\n' in follower.frames[1:])
        checkouts = Checkouts(hosted)
        try:
            await asyncio.sleep(1.5)
            idle = checkouts.count
            started = time.monotonic()
            await asyncio.to_thread(write, hosted, ALICE, task_id, token, 'data: Reading the photos\n\n')
            await wait_until(lambda: 'data: Reading the photos\n\n' in follower.frames, 5)
            woke = time.monotonic() - started
        finally:
            checkouts.close()
            await follower.close()
        return idle, woke

    idle, woke = asyncio.run(scenario())
    # Before notices, an open stream read every 0.5 s: three reads in this window.
    assert idle == 0
    assert woke < 2


def test_a_missed_notice_only_delays_progress_until_the_fallback(hosted, monkeypatch):
    monkeypatch.setattr(task_signals, 'FALLBACK_S', 1.0)
    task_id, token = running_task(hosted, ALICE)
    signals = task_signals.start(hosted.url)
    monkeypatch.setattr(signals, 'notify', lambda _task_id: None)

    async def scenario():
        await wait_until(lambda: signals.connected)
        follower = Follower(ALICE, task_id)
        await wait_until(lambda: len(follower.frames) >= 2)
        try:
            await asyncio.to_thread(write, hosted, ALICE, task_id, token, 'data: Still delivered\n\n')
            await wait_until(lambda: 'data: Still delivered\n\n' in follower.frames, 4)
        finally:
            await follower.close()

    asyncio.run(scenario())


def test_a_stream_without_a_listener_reads_as_often_as_before(hosted):
    task_id, token = running_task(hosted, ALICE)

    async def scenario():
        follower = Follower(ALICE, task_id)
        await wait_until(lambda: len(follower.frames) >= 2)
        try:
            await asyncio.to_thread(write, hosted, ALICE, task_id, token, 'data: Polled\n\n')
            await wait_until(lambda: 'data: Polled\n\n' in follower.frames, 2)
        finally:
            await follower.close()

    asyncio.run(scenario())


def test_the_listener_reconnects_after_its_connection_drops(hosted, monkeypatch):
    monkeypatch.setattr(task_signals, 'FALLBACK_S', 30.0)
    monkeypatch.setattr(task_signals, 'RETRY_S', .1)
    task_id, token = running_task(hosted, ALICE)
    signals = task_signals.start(hosted.url)

    async def scenario():
        await wait_until(lambda: signals.connected)
        dropped = signals.backend_pid
        with hosted.connect() as admin:
            assert admin.scalar(text('SELECT pg_terminate_backend(:pid)'), {'pid': dropped})
        await wait_until(lambda: signals.reconnects >= 1 and signals.connected and signals.backend_pid != dropped)
        assert task_signals.health() == {'listening': True, 'reconnects': signals.reconnects}
        follower = Follower(ALICE, task_id)
        await wait_until(lambda: len(follower.frames) >= 2)
        try:
            await asyncio.to_thread(write, hosted, ALICE, task_id, token, 'data: After the drop\n\n')
            await wait_until(lambda: 'data: After the drop\n\n' in follower.frames, 2)
        finally:
            await follower.close()

    asyncio.run(scenario())


def test_another_sellers_notice_never_shows_that_sellers_progress(hosted, monkeypatch):
    monkeypatch.setattr(task_signals, 'FALLBACK_S', 30.0)
    alice_task, _ = running_task(hosted, ALICE)
    bob_task, bob_token = running_task(hosted, BOB)
    signals = task_signals.start(hosted.url)

    async def scenario():
        await wait_until(lambda: signals.connected)
        alice = Follower(ALICE, alice_task)
        await wait_until(lambda: len(alice.frames) >= 2)
        checkouts = Checkouts(hosted)
        try:
            # Bob's progress wakes nobody following Alice's task.
            await asyncio.to_thread(write, hosted, BOB, bob_task, bob_token, 'data: Bob private words\n\n')
            await asyncio.sleep(1)
            woke_alice = checkouts.count
        finally:
            checkouts.close()
            await alice.close()
        # A notice carrying Bob's task id still reads through Alice's session.
        intruder = Follower(ALICE, bob_task)
        await wait_until(lambda: intruder.task.done())
        return woke_alice, alice.text(), intruder

    woke_alice, alice_saw, intruder = asyncio.run(scenario())
    assert woke_alice == 1  # only Bob's write took a connection
    assert 'Bob private words' not in alice_saw
    assert isinstance(intruder.error, HTTPException) and intruder.error.status_code == 404
    assert not any('Bob private words' in frame for frame in intruder.frames)


def test_notices_carry_only_the_task_id_and_fire_on_progress_and_status(hosted):
    import psycopg

    task_id, token = running_task(hosted, ALICE)
    with psycopg.connect(task_signals.listen_url(hosted.url), autocommit=True) as listener:
        listener.execute(f'LISTEN {task_signals.CHANNEL}')
        write(hosted, ALICE, task_id, token, 'data: one\n\n')
        with SellerDatabaseSession(user_id=ALICE, bind=hosted) as db:
            queue.cancel(db, task_id)
        notices = [notice.payload for notice in listener.notifies(timeout=2, stop_after=2)]
    assert notices == [task_id, task_id]


def test_the_listener_uses_supabases_session_pooler():
    from psycopg.conninfo import conninfo_to_dict

    url = 'postgresql+psycopg://anteroom_api.ref:secret@aws-0-us-west-2.pooler.supabase.com:6543/postgres?sslmode=verify-full'
    assert task_signals.listen_url(url) == (
        'postgresql://anteroom_api.ref:secret@aws-0-us-west-2.pooler.supabase.com:5432/postgres?sslmode=verify-full')
    # A local socket (the hosted sandbox) is used as it is.
    socket = conninfo_to_dict(task_signals.listen_url('postgresql+psycopg://user@/db?host=/tmp/pg'))
    assert socket == {'user': 'user', 'dbname': 'db', 'host': '/tmp/pg'}
