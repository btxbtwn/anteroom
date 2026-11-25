"""Hand a hosted Machine's scheduled work and devices to the one replacing it.

Fly's blue-green deploy starts the new Machine and moves traffic to it before
it stops the old one. The schedulers assume a newly started process means the
previous one is gone: they close rounds and mark sends a restart cut off. So
only the Machine holding the ``schedulers`` lease runs them. When a Machine is
told to stop, its schedulers start no new marketplace work; one already
talking to a marketplace gets up to ``DRAIN_GRACE_S`` to finish while this
Machine keeps renewing the lease. Then it stops its schedulers, releases the
lease and its Mac and Chrome routes, and the new Machine takes both over
within seconds.
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
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from anteroom.services.device_routes import clear_route_cache, machine_id, routing_enabled

log = logging.getLogger(__name__)

LEASE = 'schedulers'
CHROME_LEASE = 'chrome'
LEASE_S = 30
RENEW_S = 10
# How long a stopping Machine lets a running relist or scheduled publish finish.
# hosted.py waits for the handoff and the draft worker side by side, after up to
# 120 s of request draining, so the whole stop stays under Fly's kill_timeout (300 s).
DRAIN_GRACE_S = 75
# How long hosted.py waits for ``lead`` to return: the grace, stopping each
# set of scheduler loops (up to 20 s per set, two sets on one Machine) and
# releasing the lease.
HANDOFF_WAIT_S = DRAIN_GRACE_S + 45

_draining = threading.Event()
_work_lock = threading.Lock()
_work = 0
_idle = threading.Event()
_idle.set()


def begin_draining() -> None:
    """Called from the shutdown signal: hand work over before requests drain."""
    _draining.set()


def draining() -> bool:
    return _draining.is_set()


def reset() -> None:
    _draining.clear()
    clear_route_cache()


@contextmanager
def marketplace_work() -> Iterator[None]:
    """Mark scheduled marketplace work in flight, so a drain lets it finish."""
    global _work
    with _work_lock:
        _work += 1
        _idle.clear()
    try:
        yield
    finally:
        with _work_lock:
            _work -= 1
            if not _work:
                _idle.set()


def hold(engine, name: str = LEASE) -> bool:
    if not routing_enabled():
        return True
    with engine.begin() as connection:
        return bool(connection.scalar(
            text('SELECT anteroom_private.hold_process_lease(:name, :machine, :seconds)'),
            {'name': name, 'machine': machine_id(), 'seconds': LEASE_S},
        ))


def release(engine) -> None:
    clear_route_cache()
    if not routing_enabled():
        return
    try:
        with engine.begin() as connection:
            connection.execute(text('SELECT anteroom_private.release_machine(:machine)'), {'machine': machine_id()})
    except SQLAlchemyError:
        # The lease and routes still expire on their own.
        log.warning('Could not release this Machine before stopping')
    finally:
        clear_route_cache()  # Fence claims concurrent with the release transaction.


async def lead(engine, starts: dict[str, Callable[[], Callable[[], None]]],
               on_drain: Callable[[], None], grace_s: float | None = None) -> None:
    """Run each set of schedulers while this Machine holds its lease; hand over on drain.

    ``starts`` maps a lease name to what starts its schedulers and returns what
    stops them. A renewal that fails for a whole lease period stops them too,
    since another Machine may take the lease once it expires. On drain, the
    leases are renewed for up to ``grace_s`` while ``marketplace_work`` is in
    flight. Every set stops before the leases are released, so the next
    Machine never overlaps one.
    """
    stops: dict[str, Callable[[], None]] = {}
    renewed: dict[str, float] = {}

    async def renew() -> None:
        for name, start in starts.items():
            try:
                held = await asyncio.to_thread(hold, engine, name)
            except SQLAlchemyError:
                log.warning('Scheduler lease database is temporarily unavailable')
                held = None
            now = time.monotonic()
            if held:
                renewed[name] = now
                if name not in stops and not _draining.is_set():
                    stops[name] = start()
            elif name in stops and (held is False or now - renewed[name] >= LEASE_S - RENEW_S):
                log.warning('This Machine no longer holds the %s lease', name)
                await asyncio.to_thread(stops.pop(name))

    drained = False
    try:
        while not _draining.is_set():
            await renew()
            await asyncio.to_thread(_draining.wait, RENEW_S)
        clear_route_cache()
        drained = True
        on_drain()
        deadline = time.monotonic() + (DRAIN_GRACE_S if grace_s is None else grace_s)
        while stops and not _idle.is_set():
            left = deadline - time.monotonic()
            if left <= 0:
                log.warning('Scheduled marketplace work did not finish within the shutdown grace')
                break
            if await asyncio.to_thread(_idle.wait, min(RENEW_S, left)):
                break
            await renew()
    finally:
        clear_route_cache()
        if not drained:
            on_drain()
        for stop in stops.values():
            await asyncio.to_thread(stop)
        await asyncio.to_thread(release, engine)
