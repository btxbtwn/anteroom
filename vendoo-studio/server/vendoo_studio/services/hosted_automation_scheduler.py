"""Run due automations on the web, one seller at a time, with the desktop scheduler's rules.

The trusted process login finds which sellers have due or unfinished runs.
Each tick then runs inside that seller's own row-secured context. As on the
Mac, a run only changes prices and fields or queues a draft regenerate;
every marketplace write becomes an approval the seller reviews.
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
    while True:
        try:
            owners = await asyncio.to_thread(due_owners, engine)
        except SQLAlchemyError:
            log.warning('Automation schedule database is temporarily unavailable')
            owners = []
        for owner in owners:
            try:
                await run_owner(owner)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one seller's failure must not stop the others
                log.exception('Automation tick failed for one seller')
        await asyncio.sleep(TICK_SECONDS)
