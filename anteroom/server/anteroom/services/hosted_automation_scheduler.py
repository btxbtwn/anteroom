"""Run due automations on the web, each seller on their own, with the desktop scheduler's rules.

The trusted process login finds which sellers have due or unfinished runs.
Each tick then runs inside that seller's own row-secured context. As on the
Mac, a run only changes prices and fields or queues a draft regenerate;
every marketplace write becomes an approval the seller reviews.

Sellers' ticks run side by side, so one seller's long run (a regenerate can
wait minutes per draft) never holds another seller's due automation. A seller
is never ticked twice at once. A tick commits before it waits on anything
outside the app, so a waiting run holds no pooled database connection.
"""
from __future__ import annotations

import asyncio
from collections import deque
import logging
import os
import time

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from anteroom.services.automation_scheduler import TICK_SECONDS, tick
from anteroom.services.hosted_context import for_seller
from anteroom.services.service_context import use

log = logging.getLogger(__name__)

DEFAULT_SCHEDULER_CONCURRENCY = 4


def _scheduler_concurrency(override: int | None = None) -> int:
    if override is not None:
        return max(1, int(override))
    raw = os.environ.get('ANTEROOM_AUTOMATION_CONCURRENCY')
    if raw is None or not raw.strip():
        return DEFAULT_SCHEDULER_CONCURRENCY
    try:
        return max(1, int(raw.strip()))
    except ValueError:
        log.warning(
            'Invalid ANTEROOM_AUTOMATION_CONCURRENCY=%r; using default %d',
            raw, DEFAULT_SCHEDULER_CONCURRENCY,
        )
        return DEFAULT_SCHEDULER_CONCURRENCY


def due_owners(engine) -> list[str]:
    with engine.begin() as connection:
        return [row.owner_id for row in connection.execute(text('SELECT * FROM anteroom_private.due_automation_owners()'))]


async def run_owner(owner: str) -> None:
    from anteroom.database import SessionLocal

    with use(for_seller(owner)):
        with SessionLocal() as db:
            await tick(db)


async def serve(engine, *, max_concurrency: int | None = None) -> None:
    limit = _scheduler_concurrency(max_concurrency)
    running: dict[str, asyncio.Task] = {}
    queued: deque[str] = deque()
    queued_set: set[str] = set()
    next_poll = 0.0

    async def run_guarded(owner: str) -> None:
        try:
            await run_owner(owner)
        except Exception:
            log.error('Automation tick failed for one seller', exc_info=True)
        finally:
            if running.get(owner) is asyncio.current_task():
                del running[owner]

    try:
        while True:
            if time.monotonic() >= next_poll:
                try:
                    owners = await asyncio.to_thread(due_owners, engine)
                except SQLAlchemyError:
                    log.warning('Automation schedule database is temporarily unavailable')
                    owners = []
                for owner in owners:
                    if owner in running or owner in queued_set:
                        continue  # Still running or already waiting; a later poll picks up newly due work.
                    queued.append(owner)
                    queued_set.add(owner)
                next_poll = time.monotonic() + TICK_SECONDS

            while queued and len(running) < limit:
                owner = queued.popleft()
                queued_set.discard(owner)
                if owner in running:
                    continue
                running[owner] = asyncio.create_task(run_guarded(owner))

            wait_for = max(0.0, next_poll - time.monotonic())
            if queued and running:
                await asyncio.wait(running.values(), timeout=wait_for, return_when=asyncio.FIRST_COMPLETED)
            else:
                await asyncio.sleep(wait_for)
    finally:
        remaining = list(running.values())
        for task in remaining:
            task.cancel()
        await asyncio.gather(*remaining, return_exceptions=True)
