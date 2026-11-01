"""Check automatic eBay offers on the web, one seller at a time, with the Mac's rules.

The trusted process login finds which sellers have offers turned on, or a send
a restart cut off. Each check then runs in that seller's own row-secured
context with their own eBay token. As on the Mac, nothing is sent at startup:
cut-off sends are marked unknown and pause automation, and the first check
waits a full interval.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from vendoo_studio.services import ebay_offers
from vendoo_studio.services.hosted_context import for_seller
from vendoo_studio.services.service_context import use

log = logging.getLogger(__name__)


def offer_owners(engine) -> list[tuple[str, bool]]:
    """(seller, has a cut-off send) for every seller with offers to check."""
    with engine.begin() as connection:
        return [(row.owner_id, row.interrupted) for row in
                connection.execute(text('SELECT * FROM anteroom_private.ebay_offer_owners()'))]


async def _owners(engine) -> list[tuple[str, bool]]:
    try:
        return await asyncio.to_thread(offer_owners, engine)
    except SQLAlchemyError:
        log.warning('eBay offer schedule database is temporarily unavailable')
        return []


async def serve(engine) -> None:
    for owner, interrupted in await _owners(engine):
        if interrupted:
            try:
                with use(for_seller(owner)):
                    await asyncio.to_thread(ebay_offers.recover_interrupted)
            except Exception:  # noqa: BLE001 - one seller's failure must not stop the others
                log.exception('Could not pause one seller\'s interrupted eBay offers')
    while True:
        await asyncio.sleep(ebay_offers.CHECK_INTERVAL_S)
        for owner, _interrupted in await _owners(engine):
            try:
                with use(for_seller(owner)):
                    await ebay_offers.run_automation()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one seller's failure must not stop the others
                log.exception('eBay offer check failed for one seller')
