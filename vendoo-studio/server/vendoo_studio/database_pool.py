"""Bounded checkout evidence for hosted pool timeouts, without another DB read.

The checkout stack identifies who acquired a connection, not which query is
currently running. Keep only code locations and monotonic ages: never SQL,
parameters, frame locals, request data, task names or connection URLs.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.pool import QueuePool

_MAX_HOLDERS = 20
_MAX_FRAMES = 6


def _checkout_site() -> list[str]:
    sites = []
    frame = sys._getframe(1)
    try:
        # Walking code objects avoids loading source files or retaining locals.
        while frame is not None and len(sites) < _MAX_FRAMES:
            filename = frame.f_code.co_filename.replace('\\', '/')
            if '/vendoo_studio/' in filename and not filename.endswith('/database_pool.py'):
                relative = filename.rsplit('/vendoo_studio/', 1)[1]
                sites.append(f'{relative}:{frame.f_lineno} in {frame.f_code.co_name}')
            frame = frame.f_back
    finally:
        del frame
    return sites


class DiagnosticQueuePool(QueuePool):
    """QueuePool with the same capacity/waits and an in-memory timeout snapshot."""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._holders: dict[int, dict[str, Any]] = {}
        self._holders_lock = threading.Lock()

    def _remember_checkout(self, record) -> None:
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        holder = {'started': time.monotonic(), 'execution': 'async_task' if task else 'thread',
                  'checkout_stack': _checkout_site()}
        with self._holders_lock:
            self._holders[id(record)] = holder

    def _do_return_conn(self, record) -> None:
        with self._holders_lock:
            self._holders.pop(id(record), None)
        super()._do_return_conn(record)

    def _timeout_snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._holders_lock:
            holders = sorted(self._holders.values(), key=lambda holder: holder['started'])
        return {
            'captured_at': datetime.now(UTC).isoformat(timespec='milliseconds'),
            'pool_size': self.size(), 'checked_out': self.checkedout(), 'overflow': self.overflow(),
            'tracked_holders': len(holders), 'omitted_holders': max(0, len(holders) - _MAX_HOLDERS),
            'holders': [{'held_seconds': round(max(0, now - holder['started']), 3),
                         'execution': holder['execution'], 'checkout_stack': holder['checkout_stack']}
                        for holder in holders[:_MAX_HOLDERS]],
        }

    def _do_get(self):
        try:
            record = super()._do_get()
        except PoolTimeout as exc:
            # Capture before unwinding releases the connections that explain it.
            # A reporting failure must never replace the actual timeout.
            try:
                exc.__dict__['database_pool'] = self._timeout_snapshot()
            except Exception:
                pass
            raise

        try:
            self._remember_checkout(record)
        except Exception:
            pass  # Instrumentation must not strand a successfully acquired slot.
        return record
