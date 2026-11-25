"""Run seller-enabled sharing and offer rules only in their connected Chrome.

Rules are saved off and enabled explicitly by their owner. The desktop sender
retains its operation lock, caps, minimums and durable at-most-once journal.
A restarted round is closed before the first timed pass; unknown sends are
never retried. Nothing here lists, publishes or delists.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time

from anteroom.database import SessionLocal, in_worker_session, limit_worker_db_steps, run_worker_db_step
from anteroom.services import promotion_automations
from anteroom.services.hosted_browser_relay import MARKETPLACE_PROMOTIONS, ready_sellers
from anteroom.services.hosted_context import for_seller
from anteroom.services.job_watchdog import fail_stalled_jobs
from anteroom.services.service_context import use

log = logging.getLogger(__name__)

# Cloud sessions spawn a fresh worker subprocess and hit the marketplace's
# recent-likes feed directly; polling roughly every 4-7 minutes (with jitter
# and startup stagger) cuts background traffic and Vault decrypts ~5x while
# avoiding synchronized spikes across sellers after a deploy.
CLOUD_LIKE_CHECK_INTERVAL_S = 300.0
CLOUD_LIKE_JITTER_RANGE = (0.8, 1.4)
CLOUD_LIKE_INITIAL_STAGGER_S = 120.0


def _cloud_like_check_interval_s() -> float:
    raw = os.environ.get('ANTEROOM_CLOUD_LIKE_CHECK_INTERVAL_S', '').strip()
    if not raw:
        return CLOUD_LIKE_CHECK_INTERVAL_S
    try:
        return max(60.0, float(raw))
    except ValueError:
        return CLOUD_LIKE_CHECK_INTERVAL_S


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


# Leave connections for interactive requests while sellers wait independently
# for marketplace replies and pacing delays.
MAX_CONCURRENT_DB_STEPS = 2


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
                await run_worker_db_step(check_disconnected, owner)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a seller's failed round must not stop others
            log.exception('Promotion check failed for one seller; unknown sends are never retried')
    with limit_worker_db_steps(asyncio.Semaphore(MAX_CONCURRENT_DB_STEPS)):
        await asyncio.gather(*(check(owner) for owner in ready | recovered))


async def serve() -> None:
    recovered: set[str] = set()
    while True:
        # Enabling a rule or starting the API never sends as a side effect.
        await asyncio.sleep(promotion_automations.TICK_S)
        await check_ready_sellers(recovered)


def _arm_next_cloud_like_check(now: float, interval: float) -> float:
    low, high = CLOUD_LIKE_JITTER_RANGE
    return now + max(60.0, random.uniform(interval * low, interval * high))


def _due_cloud_like_markets(
    owner: str,
    marketplaces: tuple[str, ...],
    like_checks: dict[tuple[str, str], float] | None,
    *,
    now: float | None = None,
) -> tuple[str, ...]:
    if like_checks is None:
        return marketplaces
    current_time = time.monotonic() if now is None else now
    interval = _cloud_like_check_interval_s()
    due: list[str] = []
    for market in marketplaces:
        key = (owner, market)
        if current_time >= like_checks.get(key, current_time):
            due.append(market)
            like_checks[key] = _arm_next_cloud_like_check(current_time, interval)
    return tuple(due)


async def check_cloud_seller(
    owner: str,
    marketplaces: tuple[str, ...],
    recovered: set[str],
    like_checks: dict[tuple[str, str], float] | None = None,
) -> None:
    try:
        with use(for_seller(owner)):
            await in_worker_session(_prepare_seller, owner, recovered)
            due_likes = _due_cloud_like_markets(owner, marketplaces, like_checks)
            if due_likes:
                await promotion_automations.check_new_likes(marketplaces=due_likes)
            await promotion_automations.tick(marketplaces=marketplaces)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - a seller's failed round must not stop others
        log.exception('Cloud promotion check failed for one seller; unknown sends are never retried')


async def check_cloud_sellers(
    engine,
    recovered: set[str],
    like_checks: dict[tuple[str, str], float] | None = None,
    *,
    startup: bool = False,
) -> None:
    from anteroom.services import cloud_sessions

    owners_by_seller: dict[str, list[str]] = {}
    for marketplace in sorted(cloud_sessions.CLOUD_WRITE_MARKETPLACES):
        owners = await asyncio.to_thread(cloud_sessions.active_owners, engine, marketplace)
        for seller in owners:
            owners_by_seller.setdefault(seller, []).append(marketplace)
    if like_checks is not None:
        active_pairs = {(seller, market) for seller, markets in owners_by_seller.items() for market in markets}
        for stale in list(like_checks):
            if stale not in active_pairs:
                like_checks.pop(stale, None)
        if startup:
            # Spread the checks for existing connections across the first few rounds.
            now = time.monotonic()
            stagger = min(_cloud_like_check_interval_s(), CLOUD_LIKE_INITIAL_STAGGER_S)
            for seller, markets in owners_by_seller.items():
                for market in markets:
                    like_checks[(seller, market)] = now + random.uniform(0.0, stagger)
    if not owners_by_seller:
        return
    with limit_worker_db_steps(asyncio.Semaphore(MAX_CONCURRENT_DB_STEPS)):
        await asyncio.gather(*(
            check_cloud_seller(seller, tuple(markets), recovered, like_checks)
            for seller, markets in owners_by_seller.items()
        ))


async def serve_cloud(engine) -> None:
    recovered: set[str] = set()
    like_checks: dict[tuple[str, str], float] = {}
    startup = True
    while True:
        await asyncio.sleep(promotion_automations.TICK_S)
        try:
            await check_cloud_sellers(engine, recovered, like_checks, startup=startup)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a failed pass must not stop the scheduler
            log.exception('Cloud promotion check pass failed. The next scheduled pass will run.')
        else:
            startup = False
