"""Run due automations on the web, each seller on their own, with the desktop scheduler's rules.

The trusted process login finds which sellers have due or unfinished runs.
Each tick then runs inside that seller's own row-secured context. As on the
Mac, a run only changes prices and fields or queues a draft regenerate;
every marketplace write becomes an approval the seller reviews.

Sellers' ticks run side by side, so one seller's long run (a regenerate can
wait minutes per draft) never holds another seller's due automation. A seller
is never ticked twice at once. Database-active steps share a bounded semaphore
so many simultaneous due sellers cannot exhaust the connection pool, while
waiting regenerations hold neither a database connection nor a scheduler slot.
"""
from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from anteroom.services import process_handoff
from anteroom.services.automation_scheduler import TICK_SECONDS, bind_db_gate, tick
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
    db_gate = asyncio.Semaphore(_scheduler_concurrency(max_concurrency))
    running: dict[str, asyncio.Task] = {}

    async def run_guarded(owner: str) -> None:
        try:
            with bind_db_gate(db_gate):
                await run_owner(owner)
        except Exception:
            log.error('Automation tick failed for one seller', exc_info=True)
        finally:
            if running.get(owner) is asyncio.current_task():
                del running[owner]

    try:
        while True:
            try:
                owners = await asyncio.to_thread(due_owners, engine)
            except SQLAlchemyError:
                log.warning('Automation schedule database is temporarily unavailable')
                owners = []
            for owner in owners:
                if owner in running:
                    continue  # Still running; the next poll picks up any newly due work.
                if process_handoff.draining():
                    break  # This Machine is stopping; running ticks finish, the next Machine starts new ones.
                running[owner] = asyncio.create_task(run_guarded(owner))
            await asyncio.sleep(TICK_SECONDS)
    finally:
        remaining = list(running.values())
        for task in remaining:
            task.cancel()
        await asyncio.gather(*remaining, return_exceptions=True)
