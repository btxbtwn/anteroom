"""Persist hosted sales without a browser, never publishing or delisting."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from vendoo_studio.services import marketplace_accounts, recent_orders, relay_client, relay_sales
from vendoo_studio.services.hosted_context import for_seller
from vendoo_studio.services.keychain import get_marketplace_accounts
from vendoo_studio.services.service_context import use

INTERVAL_S = 30
log = logging.getLogger(__name__)


def sales_owners(engine) -> list[str]:
    with engine.begin() as connection:
        return list(connection.execute(text('SELECT owner_id FROM anteroom_private.sales_detection_owners()')).scalars())


async def check_owner(owner: str) -> None:
    context = for_seller(owner)
    with use(context):
        if not await asyncio.to_thread(get_marketplace_accounts):
            return
        try:
            response = await relay_client.relay_request('GET', '/studio/events', params={'after': 0, 'limit': 200})
            response.raise_for_status()
            through = None
            for event in response.json()['events']:
                try:
                    await relay_sales.handle_event(event, session_factory=context.session_factory,
                                                   read_etsy_receipt=marketplace_accounts.read_etsy_receipt)
                except Exception:
                    log.warning('Hosted sale event was not saved; leaving it for retry')
                    break
                through = event['seq']
            if through is not None:
                response = await relay_client.relay_request('POST', '/studio/ack', json_body={'through': through})
                response.raise_for_status()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning('Hosted sales inbox could not be checked; the next poll will retry')
        # The shared seven-day reader throttles itself to once per five minutes.
        # It also runs after an inbox failure, so missing notifications are recovered.
        await recent_orders.refresh()


async def serve(engine) -> None:
    while True:
        try:
            owners = await asyncio.to_thread(sales_owners, engine)
        except SQLAlchemyError:
            log.warning('Hosted sales schedule database is temporarily unavailable')
            owners = []
        for owner in owners:
            try:
                await check_owner(owner)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.error('Hosted sales check failed for one seller; continuing with the others')
        await asyncio.sleep(INTERVAL_S)
