"""The Mac's job watchdog on the web, one seller at a time.

The trusted process login finds sellers with a running job or an unconfirmed
marketplace action. Each pass then runs in that seller's own row-secured
context: it fails jobs this process is not running, so the marketplace slot
frees up, and reads what eBay or Etsy has for any action left unconfirmed.
It only reads marketplaces; it never lists, updates or delists.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from anteroom.services.hosted_context import for_seller
from anteroom.services.job_watchdog import SWEEP_INTERVAL_SEC, fail_stalled_jobs
from anteroom.services.service_context import use

log = logging.getLogger(__name__)


def unsettled_owners(engine) -> list[str]:
    with engine.begin() as connection:
        return [row.owner_id for row in connection.execute(text('SELECT * FROM anteroom_private.unsettled_job_owners()'))]


async def sweep_owner(owner: str, *, reconcile_all: bool) -> list[str]:
    """Fail this seller's dead jobs; read results a restart or a failure left unconfirmed."""
    from anteroom.database import SessionLocal
    from anteroom.services.marketplace_delist import reconcile_removals
    from anteroom.services.native_marketplaces import reconcile

    with use(for_seller(owner)):
        def fail_in_session() -> list[str]:
            with SessionLocal() as db:
                return fail_stalled_jobs(db)

        failed = await asyncio.to_thread(fail_in_session)
        await asyncio.gather(reconcile(chrome=True, periodic=not (failed or reconcile_all)), reconcile_removals())
    return failed


async def serve(engine) -> None:
    first = True
    active: set[str] = set()

    async def run(owner: str, startup: bool) -> None:
        try:
            await sweep_owner(owner, reconcile_all=startup)
        except Exception:  # noqa: BLE001 - one seller's failure must not stop the others
            log.exception('Job watchdog pass failed for one seller')
        finally:
            active.discard(owner)

    async with asyncio.TaskGroup() as tasks:
        while True:
            try:
                owners = await asyncio.to_thread(unsettled_owners, engine)
            except SQLAlchemyError:
                log.warning('Job watchdog database is temporarily unavailable')
                owners = []
            for owner in owners:
                if owner not in active:
                    active.add(owner)
                    tasks.create_task(run(owner, first))
            first = False
            await asyncio.sleep(SWEEP_INTERVAL_SEC)
