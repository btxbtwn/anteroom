"""Request-local elapsed times shared with worker threads. No SQL text or parameters."""
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from time import monotonic

from sqlalchemy import event


class Phases:
    def __init__(self):
        self.lock = Lock()
        self.elapsed = defaultdict(float)
        self.active = {}

    def start(self, name):
        span = object()
        with self.lock:
            self.active[span] = (name, monotonic())
        return span

    def finish(self, span):
        with self.lock:
            name, started = self.active.pop(span)
            self.elapsed[name] += monotonic() - started

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
    return (phases, phases.start(name)) if phases is not None else None


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
