"""Catch missed eBay and Etsy sale notifications while the seller uses their workspace.

Reuses read-only order recovery: never notifies, delists or changes stock.
The seven-day overlap also refreshes cancellations without trusting webhooks.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

from vendoo_studio.services import order_recovery
from vendoo_studio.services.service_context import current
from vendoo_studio.services.user_settings import sale_detection_enabled

MARKETPLACES = ("ebay", "etsy")
NAMES = {"ebay": "eBay", "etsy": "Etsy"}
INTERVAL_S = 300
_due: dict[tuple[str, str, str], float] = {}
_running: set[tuple[str, str, str]] = set()
log = logging.getLogger(__name__)


async def refresh() -> None:
    """Check each connected marketplace; one failing marketplace never stops another."""
    for marketplace in MARKETPLACES:
        await _refresh(marketplace)


async def _refresh(marketplace: str) -> None:
    from vendoo_studio.database import SessionLocal
    from vendoo_studio.services.keychain import get_marketplace_accounts

    name = NAMES[marketplace]
    try:
        if not await asyncio.to_thread(sale_detection_enabled, marketplace):
            return
        context = current()
        account = (await asyncio.to_thread(get_marketplace_accounts)).get(marketplace) or {}
    except Exception:
        log.warning("Recent %s orders could not read the connection; the next catch-up will retry", name)
        return
    connected = account.get("connectedAt")
    if not account.get("accountId") or connected is None:
        return
    key = (context.seller_id if context else "", marketplace, str(connected))
    now = time.monotonic()
    if key in _running or now < _due.get(key, 0):
        return
    _running.add(key)
    _due[key] = now + INTERVAL_S
    factory = context.session_factory if context else SessionLocal
    today = datetime.now(UTC).date()
    page, connection, through = 1, None, None
    try:
        while True:
            with factory() as db:
                result = await order_recovery.recover(db, marketplace, today - timedelta(days=6), today,
                                                     page, str(connected), connection, through)
            if result.get("truncated"):
                log.warning("Recent %s orders exceed the recovery limit; use a narrower manual recovery range", name)
            if result.get("nextPage") is None:
                break
            page = result["nextPage"]
            connection = result["connection"]
            through = datetime.fromisoformat(result["through"])
    except Exception:  # One failed catch-up must not break the workspace or expose provider payloads.
        log.warning("Recent %s orders could not be checked; the next catch-up will retry", name)
    finally:
        _running.discard(key)
