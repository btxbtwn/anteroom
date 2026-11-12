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
import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from vendoo_studio.services.automation_scheduler import TICK_SECONDS, tick
from vendoo_studio.services.hosted_context import for_seller
from vendoo_studio.services.service_context import use

log = logging.getLogger(__name__)


def due_owners(engine) -> list[str]:
    with engine.begin() as connection:
        return [row.owner_id for row in connection.execute(text('SELECT * FROM anteroom_private.due_automation_owners()'))]


async def run_owner(owner: str) -> None:
    from vendoo_studio.database import SessionLocal

    with use(for_seller(owner)):
        with SessionLocal() as db:
            await tick(db)


async def serve(engine) -> None:
    running: dict[str, asyncio.Task] = {}

    def settled(owner: str, task: asyncio.Task) -> None:
        if running.get(owner) is task:
            del running[owner]
        if not task.cancelled() and task.exception() is not None:
            log.error('Automation tick failed for one seller', exc_info=task.exception())

    try:
        while True:
            try:
                owners = await asyncio.to_thread(due_owners, engine)
            except SQLAlchemyError:
                log.warning('Automation schedule database is temporarily unavailable')
                owners = []
            for owner in owners:
                if owner in running:
                    continue  # Still on its last tick; a later loop picks up anything newly due.
                task = asyncio.create_task(run_owner(owner))
                running[owner] = task
                task.add_done_callback(lambda done, owner=owner: settled(owner, done))
            await asyncio.sleep(TICK_SECONDS)
    finally:
        remaining = list(running.values())
        for task in remaining:
            task.cancel()
        await asyncio.gather(*remaining, return_exceptions=True)
