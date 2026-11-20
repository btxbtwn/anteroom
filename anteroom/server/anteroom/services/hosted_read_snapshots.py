"""Share expensive seller reads across tabs, validated against committed changes."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic, perf_counter
from typing import Any

from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError

from anteroom.models.seller_change import SellerChange
from anteroom.models.background_task import BackgroundTask
from anteroom.services.background_tasks import ACTIVE
from anteroom.services.error_alerts import note_cause
from anteroom.services.request_timing import note
from anteroom.services.service_context import current


def _version(context):
    from anteroom.database import engine

    if engine.dialect.name != 'postgresql':
        raise HTTPException(503, 'The cloud database is not configured')
    with context.session_factory() as db:
        changes = tuple(db.execute(select(SellerChange.topic, SellerChange.changed_at)
                                   .order_by(SellerChange.topic)).all())
        # Trusted worker SQL can change durable task state without publishing
        # a feed row. Busy membership must still invalidate suggestions. Lease
        # timestamps and queued -> running do not change that membership.
        busy = tuple(db.scalars(select(BackgroundTask.conversation_id).where(
            BackgroundTask.status.in_(ACTIVE), BackgroundTask.conversation_id.isnot(None))
            .distinct().order_by(BackgroundTask.conversation_id)).all())
    return changes, busy


# Diagnostics only: where a slow read spent its time. Every value is a count,
# a millisecond total or a bucket bound, never a seller, listing or query.
_build_phases: ContextVar[dict[str, Any] | None] = ContextVar('snapshot_build_phases', default=None)
_ROW_BUCKETS = (0, 10, 100, 1_000, 10_000)


def _ms(seconds: float) -> int:
    return round(max(0.0, seconds) * 1000)


def _rows_at_most(result) -> int:
    """A coarse size for list results: the smallest bucket bound that holds it."""
    if not isinstance(result, list):
        return 0
    return next((bound for bound in _ROW_BUCKETS if len(result) <= bound), _ROW_BUCKETS[-1] + 1)


def _pool_use() -> str:
    """Checked-out connections over the base pool size, read without a query.

    More checked out than the size means overflow connections are in use.
    """
    from anteroom.database import engine

    pool = engine.pool
    checked_out, size = getattr(pool, 'checkedout', None), getattr(pool, 'size', None)
    return f'{checked_out()}/{size()}' if callable(checked_out) and callable(size) else '0/0'


async def _in_thread(function, *args):
    """Run ``function`` on the loop's thread pool; also report queued and run seconds."""
    submitted = perf_counter()
    started: list[float] = []

    def run():
        started.append(perf_counter())
        return function(*args)

    result = await asyncio.to_thread(run)
    return result, started[0] - submitted, perf_counter() - started[0]


# Fixed names only: never SQL text, parameters, seller IDs or row contents.
INVENTORY_STEPS = ('analytics', 'reconcile', 'conversations', 'covers', 'listings',
                   'direct', 'sales', 'unsent', 'assemble')
SUGGESTION_STEPS = ('preferences', 'covers', 'photo_counts', 'listing_rows',
                    'direct', 'conversations', 'busy', 'ranking')


@contextmanager
def snapshot_step(db, name: str):
    """Elapsed wall time and ORM call count, not server SQL execution time.

    The observer belongs to this session and step only. It observes ORM
    execution attempts (including failed ones), not driver round trips or
    autoflush statements. No additional queries or connections are acquired.
    """
    phases = _build_phases.get()
    if phases is None or name not in (*INVENTORY_STEPS, *SUGGESTION_STEPS):
        yield
        return
    calls = 0

    def executed(_state):
        nonlocal calls
        calls = min(calls + 1, 10000)

    event.listen(db, 'do_orm_execute', executed)
    started = perf_counter()
    try:
        yield
    finally:
        event.remove(db, 'do_orm_execute', executed)
        phases[f'step_{name}_ms'] = min(_ms(perf_counter() - started), 600000)
        phases[f'step_{name}_orm_calls'] = calls


def _watch(db, phases: dict[str, Any]) -> None:
    """Count the build's commits and stamp when its last transaction ended."""
    phases['commits'] = 0

    @event.listens_for(db, 'after_commit')
    def committed(_session):
        phases['commits'] += 1

    @event.listens_for(db, 'after_transaction_end')
    def ended(_session, transaction):
        if transaction.parent is None:
            phases['db_end'] = perf_counter()


def _load(context, read):
    phases = _build_phases.get()
    if phases is None:
        phases = {}
    started = perf_counter()
    context.settings.read()
    with context.session_factory() as db:
        _watch(db, phases)
        # Begin now so connection waits are not counted as query time.
        db.connection()
        connected = perf_counter()
        result = read(db)
    finished = perf_counter()
    # A read that releases its connection early, then assembles the response
    # in Python, splits here: database work before, CPU work after.
    db_end = min(phases.pop('db_end', finished), finished)
    phases.update(connect=connected - started, db=db_end - connected, assemble=finished - db_end)
    return result


class SellerReadSnapshots:
    def __init__(self):
        self.completed: OrderedDict[tuple[str, str], tuple[Any, Any, float]] = OrderedDict()
        self.pending: dict[tuple[str, str], asyncio.Task] = {}

    async def read(self, name: str, load: Callable, *, refresh_seconds: float | None = None):
        context = current()
        if context is None:
            raise HTTPException(401, 'A verified seller session is required')
        key = context.seller_id, name
        # Per request: how often it checked the version, waited on another
        # request's build or built itself, and the last build it depended on.
        timing = dict.fromkeys(('checks', 'waits', 'stale_peers', 'builds', 'uncached'), 0)
        seconds = dict.fromkeys(('check', 'queue', 'wait'), 0.0)
        last_build: dict[str, Any] = {}
        pool_start = _pool_use()

        def report():
            note(snapshot=name, **timing, check_ms=_ms(seconds['check']), queue_ms=_ms(seconds['queue']),
                 wait_ms=_ms(seconds['wait']),
                 **{f'build_{phase}_ms': _ms(last_build.get(phase, 0.0))
                    for phase in ('total', 'queue', 'connect', 'db', 'assemble', 'recheck')},
                 build_commits=last_build.get('commits', 0), rows_le=last_build.get('rows_le', 0),
                 pool_start=pool_start, pool_end=_pool_use(),
                 **({f'inv_{step}_{metric}': last_build.get(f'step_{step}_{metric}', 0)
                    for step in INVENTORY_STEPS for metric in ('ms', 'orm_calls')} if name == 'inventory' else
                    {f'sug_{step}_{metric}': last_build.get(f'step_{step}_{metric}', 0)
                     for step in SUGGESTION_STEPS for metric in ('ms', 'orm_calls')} if name == 'suggestions' else {}))

        async def check():
            version, queued, ran = await _in_thread(_version, context)
            timing['checks'] += 1
            seconds['check'] += ran
            seconds['queue'] += queued
            return version

        try:
            while True:
                # Only a short change-vector query runs for each request. No
                # connection or worker thread is held while waiting on a peer.
                version = await check()
                cached = self.completed.get(key)
                if (cached is not None and cached[0] == version
                        and (refresh_seconds is None or monotonic() - cached[2] < refresh_seconds)):
                    self.completed.move_to_end(key)
                    return cached[1]
                running = self.pending.get(key)
                if running is not None:
                    waited = perf_counter()
                    try:
                        _result, phases = await asyncio.shield(running)
                    finally:
                        timing['waits'] += 1
                        seconds['wait'] += perf_counter() - waited
                    last_build = phases
                    if not phases['cached']:
                        timing['stale_peers'] += 1
                    continue

                async def build(snapshot_version=version):
                    phases: dict[str, Any] = {}
                    _build_phases.set(phases)
                    started = perf_counter()
                    try:
                        result, queued, _ran = await _in_thread(_load, context, load)
                        rechecked = perf_counter()
                        # Never label a snapshot with changes committed during
                        # its build. The next read must rebuild in that case.
                        cached = await asyncio.to_thread(_version, context) == snapshot_version
                        if cached:
                            self.completed[key] = snapshot_version, result, monotonic()
                            self.completed.move_to_end(key)
                            while len(self.completed) > 64:
                                self.completed.popitem(last=False)
                        phases.update(cached=cached, queue=queued, recheck=perf_counter() - rechecked,
                                      total=perf_counter() - started, rows_le=_rows_at_most(result))
                        return result, phases
                    finally:
                        self.pending.pop(key, None)

                task = asyncio.create_task(build())
                self.pending[key] = task
                task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
                # The build owns its session. Disconnecting one tab cannot
                # close it underneath other tabs waiting for the same result.
                result, last_build = await asyncio.shield(task)
                timing['builds'] += 1
                if not last_build['cached']:
                    timing['uncached'] += 1
                return result
        except SQLAlchemyError as exc:
            note_cause(exc)
            raise HTTPException(503, 'Cloud data is temporarily unavailable', headers={'Retry-After': '5'}) from None
        finally:
            report()
