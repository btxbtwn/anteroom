"""Wake open draft and assistant streams when their task has something new.

A task's progress stays in the durable journal (``background_task_events``),
so a stream can resume after a reconnect or a deploy. What this module saves is
the reading: without it every open stream re-read Postgres twice a second,
about five round trips each time, whether or not anything had changed.

Postgres announces each change itself. Triggers on ``background_task_events``
(insert) and ``background_tasks`` (status change) call
``pg_notify('anteroom_task_events', <task id>)``, which Postgres delivers when
the writing transaction commits; that works through Supabase's transaction
pooler. The notice carries only the task id, never listing data, and only
wakes a stream: the stream still reads through its own seller session, so row
security decides what it sees.

Receiving needs a session of its own, so each web process keeps one
connection that runs ``LISTEN`` (on Supabase, the session pooler on port 5432
of the same host as the transaction pooler) on a thread, and wakes the streams
waiting in this process. A stream that hears nothing reads again after
``FALLBACK_S`` anyway, so a lost notice only delays an update. While the
listener is down, streams read every ``background_tasks.EVENT_INTERVAL`` as
they did before it existed.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from contextlib import contextmanager
from collections.abc import Iterator

import psycopg
from sqlalchemy.engine import URL, make_url

from vendoo_studio.services.background_tasks import EVENT_INTERVAL

log = logging.getLogger(__name__)

CHANNEL = 'anteroom_task_events'
# A stream reads again this long after its last read even when it heard nothing:
# the bound on how late a lost notice can make an update, and the pace of an
# idle stream's keepalives. Postgres does not drop notices to a connected
# listener, and a reconnect wakes every stream, so this is rarely what wakes one.
FALLBACK_S = 10.0
CONNECT_TIMEOUT_S = 10
# Backoff between attempts after the listening connection fails.
RETRY_S, RETRY_MAX_S = 1.0, 30.0
# How long one wait for notices lasts before the thread checks whether to stop.
RECEIVE_S = 1.0
# An idle connection is checked this often, so one the network dropped
# without a word is noticed and replaced.
CHECK_S = 30.0
# Supabase's transaction pooler cannot LISTEN; its session pooler, on the same
# host, can.
TRANSACTION_POOLER_PORT, SESSION_POOLER_PORT = 6543, 5432


def listen_url(database_url: str | URL) -> str:
    """The libpq URL the listener connects to: the app's database, in session mode."""
    url = make_url(database_url).set(drivername='postgresql')
    if url.port == TRANSACTION_POOLER_PORT:
        url = url.set(port=SESSION_POOLER_PORT)
    return url.render_as_string(hide_password=False)


class Subscription:
    """One open stream's interest in one task. Register before reading, so no notice falls in between."""

    def __init__(self, signals: TaskSignals | None, task_id: str):
        self.signals, self.task_id = signals, task_id
        self.loop = asyncio.get_running_loop()
        self.event = asyncio.Event()

    def clear(self) -> None:
        """Call just before reading: a notice that arrives during the read wakes the next wait."""
        self.event.clear()

    async def wait(self) -> None:
        """Wait for a notice (or a listener change), or the fallback."""
        connected = self.signals is not None and self.signals.connected
        try:
            async with asyncio.timeout(FALLBACK_S if connected else EVENT_INTERVAL):
                await self.event.wait()
        except TimeoutError:
            pass

    def wake(self) -> None:
        """From any thread."""
        try:
            self.loop.call_soon_threadsafe(self.event.set)
        except RuntimeError:
            pass  # The stream's loop has closed; it no longer waits.


class TaskSignals:
    """The process's one listening connection, and the streams it wakes."""

    def __init__(self, database_url: str | URL):
        self.url = listen_url(database_url)
        self.connected = False
        self.reconnects = 0
        self.backend_pid: int | None = None
        self._subscriptions: dict[str, set[Subscription]] = {}
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._run, name='anteroom-task-signals', daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self._thread.join(timeout=RECEIVE_S + 5)

    def add(self, subscription: Subscription) -> None:
        with self._lock:
            self._subscriptions.setdefault(subscription.task_id, set()).add(subscription)

    def remove(self, subscription: Subscription) -> None:
        with self._lock:
            waiting = self._subscriptions.get(subscription.task_id)
            if waiting is not None:
                waiting.discard(subscription)
                if not waiting:
                    del self._subscriptions[subscription.task_id]

    def notify(self, task_id: str) -> None:
        with self._lock:
            waiting = list(self._subscriptions.get(task_id, ()))
        for subscription in waiting:
            subscription.wake()

    def _wake_all(self) -> None:
        # Notices sent while the listener was down are lost; every stream reads once to catch up.
        with self._lock:
            waiting = [subscription for group in self._subscriptions.values() for subscription in group]
        for subscription in waiting:
            subscription.wake()

    def _run(self) -> None:
        delay = RETRY_S
        while not self._stopping.is_set():
            try:
                self._listen()
                delay = RETRY_S
            except (psycopg.Error, OSError) as exc:
                # Never the URL or the server's message: either may carry the login.
                log.warning('Draft progress listener lost its database connection (%s); '
                            'open streams read every %.1f s until it reconnects', type(exc).__name__, EVENT_INTERVAL)
            finally:
                if self.connected:
                    self.connected = False
                    self._wake_all()
                self.backend_pid = None
            if self._stopping.wait(delay):
                return
            delay = min(delay * 2, RETRY_MAX_S)
            self.reconnects += 1

    def _listen(self) -> None:
        with psycopg.connect(self.url, autocommit=True, connect_timeout=CONNECT_TIMEOUT_S,
                             keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3) as connection:
            connection.execute(f'LISTEN {CHANNEL}')
            self.backend_pid = connection.info.backend_pid
            self.connected = True
            log.info('Draft progress listener connected (reconnects so far: %d)', self.reconnects)
            self._wake_all()
            checked = time.monotonic()
            while not self._stopping.is_set():
                for notice in connection.notifies(timeout=RECEIVE_S):
                    self.notify(notice.payload)
                if time.monotonic() - checked >= CHECK_S:
                    connection.execute('SELECT 1')
                    checked = time.monotonic()


_signals: TaskSignals | None = None


def start(database_url: str | URL) -> TaskSignals:
    global _signals
    stop()
    _signals = TaskSignals(database_url)
    _signals.start()
    return _signals


def stop() -> None:
    global _signals
    signals, _signals = _signals, None
    if signals is not None:
        signals.stop()


def health() -> dict:
    """Listener state for the health check, never a payload or the URL."""
    if _signals is None:
        return {'listening': False, 'reconnects': 0}
    return {'listening': _signals.connected, 'reconnects': _signals.reconnects}


@contextmanager
def subscribe(task_id: str) -> Iterator[Subscription]:
    """Follow one task's notices for the life of a stream."""
    signals = _signals
    subscription = Subscription(signals, task_id)
    if signals is not None:
        signals.add(subscription)
    try:
        yield subscription
    finally:
        if signals is not None:
            signals.remove(subscription)
