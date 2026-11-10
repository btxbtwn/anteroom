"""Run seller-enabled sharing and offer rules only in their connected Chrome.

Rules are saved off and enabled explicitly by their owner. The desktop sender
retains its operation lock, caps, minimums and durable at-most-once journal.
A restarted round is closed before the first timed pass; unknown sends are
never retried. Nothing here lists, publishes or delists.
"""
from __future__ import annotations

import asyncio
import logging

from vendoo_studio.database import SessionLocal
from vendoo_studio.services import promotion_automations
from vendoo_studio.services.hosted_browser_relay import MARKETPLACE_PROMOTIONS, ready_sellers
from vendoo_studio.services.hosted_context import for_seller
from vendoo_studio.services.job_watchdog import fail_stalled_jobs
from vendoo_studio.services.service_context import use

log = logging.getLogger(__name__)


async def check_seller(owner: str, recovered: set[str]) -> None:
    with use(for_seller(owner)), SessionLocal() as db:
        promotion_automations.report_overdue(db)
        if owner not in recovered:
            fail_stalled_jobs(db)
            promotion_automations.recover(db)
            recovered.add(owner)
        await promotion_automations.check_new_likes(db)
        await promotion_automations.tick(db)


async def check_ready_sellers(recovered: set[str]) -> None:
    ready = set(ready_sellers(MARKETPLACE_PROMOTIONS))

    def check_disconnected(owner):
        with use(for_seller(owner)), SessionLocal() as db:
            promotion_automations.report_overdue(db)

    async def check(owner):
        try:
            if owner in ready:
                await check_seller(owner, recovered)
            else:
                await asyncio.to_thread(check_disconnected, owner)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a seller's failed round must not stop others
            log.exception('Promotion check failed for one seller; unknown sends are never retried')
    await asyncio.gather(*(check(owner) for owner in ready | recovered))


async def serve() -> None:
    recovered: set[str] = set()
    while True:
        # Enabling a rule or starting the API never sends as a side effect.
        await asyncio.sleep(promotion_automations.TICK_S)
        await check_ready_sellers(recovered)
