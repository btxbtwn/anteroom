"""Run seller-enabled sharing and offer rules only in their connected Chrome.

Rules are saved off and enabled explicitly by their owner. The desktop sender
retains its operation lock, caps, minimums and durable at-most-once journal.
A restarted round is closed before the first timed pass; unknown sends are
never retried. Nothing here lists, publishes or delists.
"""
from __future__ import annotations

import asyncio
import logging

from anteroom.database import SessionLocal, in_worker_session
from anteroom.services import promotion_automations
from anteroom.services.hosted_browser_relay import MARKETPLACE_PROMOTIONS, ready_sellers
from anteroom.services.hosted_context import for_seller
from anteroom.services.job_watchdog import fail_stalled_jobs
from anteroom.services.service_context import use

log = logging.getLogger(__name__)


def _prepare_seller(db, owner: str, recovered: set[str]) -> None:
    promotion_automations.report_overdue(db)
    if owner not in recovered:
        fail_stalled_jobs(db)
        promotion_automations.recover(db)
        recovered.add(owner)


async def check_seller(owner: str, recovered: set[str]) -> None:
    with use(for_seller(owner)):
        await in_worker_session(_prepare_seller, owner, recovered)
        await promotion_automations.check_new_likes(skip_cloud=True)
        await promotion_automations.tick(skip_cloud=True)


# Bound concurrent promotion checks on the web Machine so periodic 60s ticks
# never crowd out interactive page loads on the shared DB pool.
MAX_CONCURRENT_SELLERS = 2


async def check_ready_sellers(recovered: set[str]) -> None:
    ready = set(ready_sellers(MARKETPLACE_PROMOTIONS))
    slots = asyncio.Semaphore(MAX_CONCURRENT_SELLERS)

    def check_disconnected(owner):
        with use(for_seller(owner)), SessionLocal() as db:
            promotion_automations.report_overdue(db)

    async def check(owner):
        try:
            async with slots:
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


async def check_cloud_seller(owner: str, marketplaces: tuple[str, ...], recovered: set[str]) -> None:
    try:
        with use(for_seller(owner)):
            await in_worker_session(_prepare_seller, owner, recovered)
            await promotion_automations.check_new_likes(marketplaces=marketplaces)
            await promotion_automations.tick(marketplaces=marketplaces)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - a seller's failed round must not stop others
        log.exception('Cloud promotion check failed for one seller; unknown sends are never retried')


async def check_cloud_sellers(engine, recovered: set[str]) -> None:
    from anteroom.services import cloud_sessions

    owners_by_seller: dict[str, list[str]] = {}
    for marketplace in sorted(cloud_sessions.SUPPORTED_CLOUD_MARKETPLACES):
        owners = await asyncio.to_thread(cloud_sessions.active_owners, engine, marketplace)
        for seller in owners:
            owners_by_seller.setdefault(seller, []).append(marketplace)
    if not owners_by_seller:
        return
    await asyncio.gather(*(
        check_cloud_seller(seller, tuple(markets), recovered)
        for seller, markets in owners_by_seller.items()
    ))


async def serve_cloud(engine) -> None:
    recovered: set[str] = set()
    while True:
        await asyncio.sleep(promotion_automations.TICK_S)
        await check_cloud_sellers(engine, recovered)
