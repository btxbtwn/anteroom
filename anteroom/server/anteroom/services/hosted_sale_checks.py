"""Check Chrome orders only for sellers with a connected, capable browser.

The desktop reader runs in each seller's row-secured context. Browser sign-ins
stay in their Chrome, and a disconnected seller is skipped until they return.
"""
from __future__ import annotations

import asyncio
import logging

from anteroom.services import chrome_sales, order_history, sale_delist_followup
from anteroom.services.hosted_browser_relay import ready_sellers
from anteroom.services.hosted_context import for_seller
from anteroom.services.service_context import use

log = logging.getLogger(__name__)


# Bound concurrent Chrome sale checks on the web Machine so periodic 30s ticks
# never crowd out interactive page loads on the shared DB pool.
MAX_CONCURRENT_SELLERS = 2


async def check_seller(seller: str) -> None:
    try:
        with use(for_seller(seller)):
            await chrome_sales.check_due()
            sale_delist_followup.start()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - a failed seller must not stop other checks
        log.exception('Chrome sale check failed for one seller')


async def check_ready_sellers() -> None:
    slots = asyncio.Semaphore(MAX_CONCURRENT_SELLERS)

    async def bounded(seller: str) -> None:
        async with slots:
            await check_seller(seller)

    await asyncio.gather(*(bounded(seller) for seller in ready_sellers(order_history.CAPABILITY)))


async def serve() -> None:
    while True:
        await asyncio.sleep(chrome_sales.POLL_INTERVAL_S)
        await check_ready_sellers()


async def check_cloud_seller(seller: str, marketplaces: tuple[str, ...]) -> None:
    try:
        with use(for_seller(seller)):
            await chrome_sales.check_due(marketplaces=marketplaces)
            sale_delist_followup.start()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - a failed seller must not stop other checks
        log.exception('Cloud sale check failed for one seller')


async def check_cloud_sellers(engine) -> None:
    from anteroom.services import cloud_sessions

    owners_by_seller: dict[str, list[str]] = {}
    for marketplace in sorted(cloud_sessions.SUPPORTED_CLOUD_MARKETPLACES):
        owners = await asyncio.to_thread(cloud_sessions.active_owners, engine, marketplace)
        for seller in owners:
            owners_by_seller.setdefault(seller, []).append(marketplace)
    if not owners_by_seller:
        return
    await asyncio.gather(*(
        check_cloud_seller(seller, tuple(markets))
        for seller, markets in owners_by_seller.items()
    ))


async def serve_cloud(engine) -> None:
    while True:
        await asyncio.sleep(chrome_sales.POLL_INTERVAL_S)
        await check_cloud_sellers(engine)
