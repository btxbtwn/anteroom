"""Refresh Sourcing on the web in the background, one seller at a time, with the Mac timer's rules.

The trusted process login finds which sellers' saved buy lists are due for a
refresh. Each refresh then runs inside that seller's own row-secured context,
under the same per-seller lock as a refresh started from the Sourcing page, so
the two never run together. A refresh only reads stores and marketplaces and
saves the seller's buy list; it never buys or lists anything.
"""
from __future__ import annotations

import asyncio
import logging
import threading

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from vendoo_studio.services import box_scout
from vendoo_studio.services.hosted_context import for_seller
from vendoo_studio.services.service_context import use

log = logging.getLogger(__name__)


def due_owners(engine) -> list[str]:
    now = box_scout._now()
    with engine.begin() as connection:
        rows = connection.execute(
            text('SELECT * FROM anteroom_private.due_sourcing_owners('
                 'CAST(:format AS integer), CAST(:plan_keys AS text[]), :refreshed_before, :now)'),
            {'format': box_scout.SNAPSHOT_FORMAT, 'plan_keys': list(box_scout.PLAN_PREFS),
             'refreshed_before': (now - box_scout.REFRESH_INTERVAL).isoformat(), 'now': now.isoformat()})
        return [row.owner_id for row in rows]


def refresh_owner(owner: str) -> bool:
    with use(for_seller(owner)):
        return box_scout.refresh_if_due()


async def _refresh_in_daemon_thread(owner: str) -> None:
    """A refresh can take minutes; like the page's refresh it must not hold up shutdown."""
    loop = asyncio.get_running_loop()
    done: asyncio.Future[None] = loop.create_future()

    def settle(error: Exception | None) -> None:
        if done.done():
            return
        if error is None:
            done.set_result(None)
        else:
            done.set_exception(error)

    def run() -> None:
        error = None
        try:
            refresh_owner(owner)
        except Exception as raised:  # noqa: BLE001 - handed to the timer, which logs it
            error = raised
        try:
            loop.call_soon_threadsafe(settle, error)
        except RuntimeError:  # the app shut down while this refresh ran
            pass

    threading.Thread(target=run, name='sourcing-refresh', daemon=True).start()
    await done


async def serve(engine) -> None:
    while True:
        try:
            owners = await asyncio.to_thread(due_owners, engine)
        except SQLAlchemyError:
            log.warning('Sourcing schedule database is temporarily unavailable')
            owners = []
        for owner in owners:
            try:
                await _refresh_in_daemon_thread(owner)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one seller's failure must not stop the others
                log.exception('Sourcing refresh failed for one seller')
        await asyncio.sleep(box_scout.TIMER_INTERVAL.total_seconds())
