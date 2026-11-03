"""Check Chrome orders only for sellers with a connected, capable browser.

The desktop reader runs in each seller's row-secured context. Browser sign-ins
stay in their Chrome, and a disconnected seller is skipped until they return.
"""
from __future__ import annotations

import asyncio
import logging

from vendoo_studio.services import chrome_sales, order_history
from vendoo_studio.services.hosted_browser_relay import ready_sellers
from vendoo_studio.services.hosted_context import for_seller
from vendoo_studio.services.service_context import use

log = logging.getLogger(__name__)


async def check_seller(seller: str) -> None:
    try:
        with use(for_seller(seller)):
            await chrome_sales.check_due()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - a failed seller must not stop other checks
        log.exception('Chrome sale check failed for one seller')


async def check_ready_sellers() -> None:
    await asyncio.gather(*(check_seller(seller) for seller in ready_sellers(order_history.CAPABILITY)))


async def serve() -> None:
    while True:
        await asyncio.sleep(chrome_sales.POLL_INTERVAL_S)
        await check_ready_sellers()
