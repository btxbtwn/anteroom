"""Request-local elapsed times shared with worker threads. No SQL text or parameters."""
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from time import monotonic

from sqlalchemy import event


# Values and field cardinality are fixed even if a future caller passes a dynamic label.
_PHASES = frozenset({'db_sql_ms', 'db_held_ms', 'db_pool_wait_ms', 'db_acquire_ms', 'db_read_ms', 'encode_ms'})


class Phases:
    def __init__(self):
        self.lock = Lock()
        self.elapsed = defaultdict(float)
        self.active = {}
        self.closed = False

    def start(self, name):
        if self.closed or name not in _PHASES:
            return None
        with self.lock:
            if self.closed:
                return None
            span = object()
            self.active[span] = (name, monotonic())
            return span

    def finish(self, span):
        with self.lock:
            entry = self.active.pop(span, None)
            if entry is not None:
                name, started = entry
                self.elapsed[name] += monotonic() - started

    def close(self):
        # A child task or worker thread may outlive its ASGI request. Freeze
        # outstanding times here, then ignore late completions and new work.
        with self.lock:
            self.closed = True
            if self.active:
                now = monotonic()
                for name, started in self.active.values():
                    self.elapsed[name] += now - started
                self.active.clear()

    def snapshot(self):
        with self.lock:
            totals = self.elapsed.copy()
            now = monotonic()
            for name, started in self.active.values():
                totals[name] += now - started
        return {name: round(seconds * 1000) for name, seconds in sorted(totals.items())}


current: ContextVar[Phases | None] = ContextVar('request_phases', default=None)


def start(name):
    phases = current.get()
    if phases is not None and (span := phases.start(name)) is not None:
        return phases, span
    return None


def finish(timer):
    if timer is not None:
        phases, span = timer
        phases.finish(span)


@contextmanager
def measure(name):
    timer = start(name)
    try:
        yield
    finally:
        finish(timer)


def observe_sql(engine):
    @event.listens_for(engine, 'before_cursor_execute')
    def before(_conn, _cursor, _sql, _params, context, _many):
        context._anteroom_sql_timer = start('db_sql_ms')

    def finished(context):
        if context is not None:
            finish(getattr(context, '_anteroom_sql_timer', None))
            context._anteroom_sql_timer = None

    @event.listens_for(engine, 'after_cursor_execute')
    def after(_conn, _cursor, _sql, _params, context, _many):
        finished(context)

    @event.listens_for(engine, 'handle_error')
    def failed(error):
        finished(error.execution_context)
