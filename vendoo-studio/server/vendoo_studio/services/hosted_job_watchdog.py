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

from vendoo_studio.services.hosted_context import for_seller
from vendoo_studio.services.job_watchdog import SWEEP_INTERVAL_SEC, fail_stalled_jobs
from vendoo_studio.services.service_context import use

log = logging.getLogger(__name__)


def unsettled_owners(engine) -> list[str]:
    with engine.begin() as connection:
        return [row.owner_id for row in connection.execute(text('SELECT * FROM anteroom_private.unsettled_job_owners()'))]


async def sweep_owner(owner: str, *, reconcile_all: bool) -> list[str]:
    """Fail this seller's dead jobs; read results a restart or a failure left unconfirmed."""
    from vendoo_studio.database import SessionLocal
    from vendoo_studio.services.native_marketplaces import reconcile

    with use(for_seller(owner)):
        def fail_in_session() -> list[str]:
            with SessionLocal() as db:
                return fail_stalled_jobs(db)

        failed = await asyncio.to_thread(fail_in_session)
        if failed or reconcile_all:
            await reconcile(chrome=False)
    return failed


async def serve(engine) -> None:
    first = True
    while True:
        try:
            owners = await asyncio.to_thread(unsettled_owners, engine)
        except SQLAlchemyError:
            log.warning('Job watchdog database is temporarily unavailable')
            owners = []
        for owner in owners:
            try:
                await sweep_owner(owner, reconcile_all=first)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one seller's failure must not stop the others
                log.exception('Job watchdog pass failed for one seller')
        first = False
        await asyncio.sleep(SWEEP_INTERVAL_SEC)
