"""Hand a hosted Machine's scheduled work and devices to the one replacing it.

Fly's blue-green deploy starts the new Machine and moves traffic to it before
it stops the old one. The schedulers assume a newly started process means the
previous one is gone: they close rounds and mark sends a restart cut off. So
only the Machine holding the ``schedulers`` lease runs them. When a Machine is
told to stop, it stops its schedulers first, then releases the lease and its
Mac and Chrome routes, and the new Machine takes both over within seconds.
Without a Fly Machine id (the Mac, the sandbox, tests) there is one process,
and it runs the schedulers without a lease.

Each lease covers one set of schedulers. ``schedulers`` covers those that only
need the database and marketplace APIs; ``chrome`` covers those that act in a
seller's Chrome, which run on the Machine Chrome connects to (the web role),
since only that process knows which tabs are connected.
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
CHROME_LEASE = 'chrome'
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


def hold(engine, name: str = LEASE) -> bool:
    if not routing_enabled():
        return True
    with engine.begin() as connection:
        return bool(connection.scalar(
            text('SELECT anteroom_private.hold_process_lease(:name, :machine, :seconds)'),
            {'name': name, 'machine': machine_id(), 'seconds': LEASE_S},
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


async def lead(engine, starts: dict[str, Callable[[], Callable[[], None]]],
               on_drain: Callable[[], None]) -> None:
    """Run each set of schedulers while this Machine holds its lease; hand over on drain.

    ``starts`` maps a lease name to what starts its schedulers and returns what
    stops them. A renewal that fails for a whole lease period stops them too,
    since another Machine may take the lease once it expires. Every set stops
    before the leases are released, so the next Machine never overlaps one.
    """
    stops: dict[str, Callable[[], None]] = {}
    renewed: dict[str, float] = {}
    try:
        while not _draining.is_set():
            for name, start in starts.items():
                try:
                    held = await asyncio.to_thread(hold, engine, name)
                except SQLAlchemyError:
                    log.warning('Scheduler lease database is temporarily unavailable')
                    held = None
                now = time.monotonic()
                if held:
                    renewed[name] = now
                    if name not in stops:
                        stops[name] = start()
                elif name in stops and (held is False or now - renewed[name] >= LEASE_S - RENEW_S):
                    log.warning('This Machine no longer holds the %s lease', name)
                    await asyncio.to_thread(stops.pop(name))
            await asyncio.to_thread(_draining.wait, RENEW_S)
    finally:
        on_drain()
        for stop in stops.values():
            await asyncio.to_thread(stop)
        await asyncio.to_thread(release, engine)
