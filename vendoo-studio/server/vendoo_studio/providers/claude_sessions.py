"""Warm Claude Code processes, reused from one model call to the next.

A model call used to start its own Claude Code process: about 0.85 CPU-s and a
240 MB process before the first token. A warm process answers the next call
for about 0.1 CPU-s. One process holds one conversation, so a call takes an
idle process (or starts one), and ``/clear`` empties it before the next call.

Only the server's event loop keeps processes warm. A call on any other loop
(``asyncio.run`` in a background thread) gets a process that closes when the
call ends, since nothing would be left to close it after that loop is gone.

Live processes are capped. Every listing caps itself at a few calls
(``model_slots``), but twenty listings at once would still start sixty
processes; past the cap a call waits for a free one.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("vendoo_studio.claude")

MAX_PROCESSES = 12
IDLE_CLOSE_SEC = 180.0


@dataclass
class _Session:
    client: Any
    key: tuple
    model: str | None = None
    idle_since: float = 0.0


class _Pool:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.idle: dict[tuple, list[_Session]] = {}
        self.live = 0
        self.freed = asyncio.Condition()
        self.reaper: asyncio.TimerHandle | None = None
        self.closing: set[asyncio.Task] = set()
        self.releasing: set[asyncio.Task] = set()

    def release_later(self, session: _Session, *, healthy: bool) -> None:
        """Hand the process back without making the caller wait for ``/clear``."""
        task = self.loop.create_task(self.release(session, healthy=healthy))
        self.releasing.add(task)
        task.add_done_callback(self.releasing.discard)

    def _close_later(self, session: _Session) -> None:
        task = self.loop.create_task(_disconnect(session.client))
        self.closing.add(task)
        task.add_done_callback(self.closing.discard)

    async def _release_slot(self) -> None:
        async with self.freed:
            self.live -= 1
            self.freed.notify()

    def _take_idle(self, key: tuple) -> _Session | None:
        sessions = self.idle.get(key)
        return sessions.pop() if sessions else None

    def _evict_one_idle(self) -> bool:
        """Close the longest-idle process of any kind to make room."""
        oldest: _Session | None = None
        for sessions in self.idle.values():
            for session in sessions:
                if oldest is None or session.idle_since < oldest.idle_since:
                    oldest = session
        if oldest is None:
            return False
        self.idle[oldest.key].remove(oldest)
        self.live -= 1
        self._close_later(oldest)
        return True

    async def acquire(self, key: tuple, make_options: Callable[[str | None], Any], model: str | None) -> _Session:
        async with self.freed:
            while True:
                session = self._take_idle(key)
                if session is not None:
                    return session
                if self.live < MAX_PROCESSES or self._evict_one_idle():
                    self.live += 1
                    break
                await self.freed.wait()
        try:
            client = await _connect(make_options(model))
        except BaseException:
            await self._release_slot()
            raise
        return _Session(client=client, key=key, model=model)

    async def release(self, session: _Session, *, healthy: bool) -> None:
        if healthy:
            try:
                await _clear(session.client)
            except Exception:
                log.warning("could not clear a Claude Code process; closing it", exc_info=True)
                healthy = False
        if not healthy:
            self._close_later(session)
            await self._release_slot()
            return
        async with self.freed:
            session.idle_since = time.monotonic()
            self.idle.setdefault(session.key, []).append(session)
            self.freed.notify()
        self._schedule_reaper()

    def _schedule_reaper(self) -> None:
        if self.reaper is None:
            self.reaper = self.loop.call_later(IDLE_CLOSE_SEC, self._reap)

    def _reap(self) -> None:
        self.reaper = None
        cutoff = time.monotonic() - IDLE_CLOSE_SEC
        remaining = False
        for key, sessions in list(self.idle.items()):
            keep = []
            for session in sessions:
                if session.idle_since <= cutoff:
                    self.live -= 1
                    self._close_later(session)
                else:
                    keep.append(session)
            self.idle[key] = keep
            remaining = remaining or bool(keep)
        if remaining:
            self._schedule_reaper()

    async def close_all(self) -> None:
        if self.releasing:
            await asyncio.gather(*self.releasing, return_exceptions=True)
        if self.reaper is not None:
            self.reaper.cancel()
            self.reaper = None
        for sessions in self.idle.values():
            for session in sessions:
                self.live -= 1
                self._close_later(session)
        self.idle.clear()
        if self.closing:
            await asyncio.gather(*self.closing, return_exceptions=True)


_pool: _Pool | None = None


def keep_warm_on_this_loop() -> None:
    """Keep processes warm for calls on the running loop (the server's)."""
    global _pool
    _pool = _Pool(asyncio.get_running_loop())


async def close_warm_processes() -> None:
    global _pool
    pool, _pool = _pool, None
    if pool is not None:
        await pool.close_all()


def _warm_pool() -> _Pool | None:
    if _pool is None:
        return None
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        return None
    return _pool if _pool.loop is running else None


async def _connect(options: Any) -> Any:
    from claude_agent_sdk import ClaudeSDKClient

    client = ClaudeSDKClient(options)
    try:
        await client.connect()
    except BaseException:
        with contextlib.suppress(Exception):
            await client.disconnect()
        raise
    return client


async def _disconnect(client: Any) -> None:
    with contextlib.suppress(Exception):
        await client.disconnect()


async def _clear(client: Any) -> None:
    await client.query("/clear")
    async for _ in client.receive_response():
        pass


@contextlib.asynccontextmanager
async def session(key: tuple, make_options: Callable[[str | None], Any], model: str | None) -> AsyncIterator[Any]:
    """A connected Claude Code client on ``model`` with an empty conversation.

    ``key`` names everything fixed when the process starts (system prompt,
    tools, turn limit, effort); processes are only shared between calls with
    the same key. ``make_options(model)`` builds the options for a new
    process. A call that fails or is cancelled closes its process, since
    it may still be answering.
    """
    pool = _warm_pool()
    if pool is None:
        client = await _connect(make_options(model))
        try:
            yield client
        finally:
            await asyncio.shield(_disconnect(client))
        return

    held = await pool.acquire(key, make_options, model)
    healthy = False
    try:
        if model and model != held.model:
            await held.client.set_model(model)
            held.model = model
        yield held.client
        healthy = True
    finally:
        pool.release_later(held, healthy=healthy)
