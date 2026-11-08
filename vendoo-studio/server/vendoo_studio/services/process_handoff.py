"""Hand a hosted Machine's scheduled work and devices to the one replacing it.

Fly's blue-green deploy starts the new Machine and moves traffic to it before
it stops the old one. The schedulers assume a newly started process means the
previous one is gone: they close rounds and mark sends a restart cut off. So
only the Machine holding the ``schedulers`` lease runs them. When a Machine is
told to stop, it stops its schedulers first, then releases the lease and its
Mac and Chrome routes, and the new Machine takes both over within seconds.
Without a Fly Machine id (the Mac, the sandbox, tests) there is one process,
and it runs the schedulers without a lease.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from vendoo_studio.services.device_routes import machine_id, routing_enabled

log = logging.getLogger(__name__)

LEASE = 'schedulers'
LEASE_S = 30
RENEW_S = 10

_draining = threading.Event()


def begin_draining() -> None:
    """Called from the shutdown signal: hand work over before requests drain."""
    _draining.set()


def draining() -> bool:
    return _draining.is_set()


def reset() -> None:
    _draining.clear()


def hold(engine) -> bool:
    if not routing_enabled():
        return True
    with engine.begin() as connection:
        return bool(connection.scalar(
            text('SELECT anteroom_private.hold_process_lease(:name, :machine, :seconds)'),
            {'name': LEASE, 'machine': machine_id(), 'seconds': LEASE_S},
        ))


def release(engine) -> None:
    if not routing_enabled():
        return
    try:
        with engine.begin() as connection:
            connection.execute(text('SELECT anteroom_private.release_machine(:machine)'), {'machine': machine_id()})
    except SQLAlchemyError:
        # The lease and routes still expire on their own.
        log.warning('Could not release this Machine before stopping')


async def lead(engine, start: Callable[[], Callable[[], None]] | None, on_drain: Callable[[], None]) -> None:
    """Run the schedulers while this Machine holds the lease; hand over on drain.

    ``start`` starts the schedulers and returns what stops them. A renewal that
    fails for a whole lease period stops them too, since another Machine may
    take the lease once it expires.
    """
    stop: Callable[[], None] | None = None
    renewed = 0.0
    try:
        while not _draining.is_set():
            if start is not None:
                try:
                    held = await asyncio.to_thread(hold, engine)
                except SQLAlchemyError:
                    log.warning('Scheduler lease database is temporarily unavailable')
                    held = None
                now = time.monotonic()
                if held:
                    renewed = now
                    if stop is None:
                        stop = start()
                elif stop is not None and (held is False or now - renewed >= LEASE_S - RENEW_S):
                    log.warning('This Machine no longer holds the scheduler lease')
                    await asyncio.to_thread(stop)
                    stop = None
            await asyncio.to_thread(_draining.wait, RENEW_S)
    finally:
        on_drain()
        if stop is not None:
            await asyncio.to_thread(stop)
        await asyncio.to_thread(release, engine)
