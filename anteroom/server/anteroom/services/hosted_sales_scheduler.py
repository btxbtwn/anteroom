"""Record hosted relay sales and run the seller's opt-in post-sale actions."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from anteroom.services import marketplace_accounts, recent_orders, relay_client, relay_sales, sale_delist_followup
from anteroom.services.hosted_context import for_seller
from anteroom.services.keychain import get_marketplace_accounts
from anteroom.services.service_context import use

INTERVAL_S = 30
MAX_CONCURRENT_SELLERS = 2
MAX_EVENT_FAILURES = 3
log = logging.getLogger(__name__)


def sales_owners(engine) -> list[str]:
    with engine.begin() as connection:
        return list(connection.execute(text('SELECT owner_id FROM anteroom_private.sales_detection_owners()')).scalars())


async def check_owner(owner: str, event_failures: dict[int, int]) -> None:
    context = for_seller(owner)
    with use(context):
        # Sales from every source, the inbox below included, are followed until delisted elsewhere.
        sale_delist_followup.start()
        if not await asyncio.to_thread(get_marketplace_accounts):
            return
        try:
            response = await relay_client.relay_request('GET', '/studio/events', params={'after': 0, 'limit': 200})
            response.raise_for_status()
            events = response.json()['events']
            for seq in event_failures.keys() - {event['seq'] for event in events}:
                del event_failures[seq]
            through = None
            sale_ids: list[str] = []
            for event in events:
                seq = event['seq']
                attempts = event_failures.get(seq, 0)
                if attempts == MAX_EVENT_FAILURES:
                    # The previous acknowledgement failed. Keep this event skipped.
                    through = seq
                    continue
                try:
                    created = await relay_sales.handle_event(event, session_factory=context.session_factory,
                                                             read_etsy_receipt=marketplace_accounts.read_etsy_receipt)
                except Exception as exc:
                    attempts += 1
                    event_failures[seq] = attempts
                    if attempts < MAX_EVENT_FAILURES:
                        log.warning('Hosted sale event was not saved; leaving it for retry')
                        break
                    log.error('Skipping hosted relay event %s (%s) after %d failed attempts (%s)',
                              seq, event.get('eventType'), attempts, type(exc).__name__)
                else:
                    event_failures.pop(seq, None)
                    sale_ids.extend(created)
                through = seq
            if sale_ids:
                await marketplace_accounts.announce_sales(sale_ids, session_factory=context.session_factory)
            if through is not None:
                response = await relay_client.relay_request('POST', '/studio/ack', json_body={'through': through})
                response.raise_for_status()
                for seq in tuple(event_failures):
                    if seq <= through:
                        del event_failures[seq]
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning('Hosted sales inbox could not be checked; the next poll will retry')
        # The shared seven-day reader throttles itself to once per five minutes.
        # It also runs after an inbox failure, so missing notifications are recovered.
        await recent_orders.refresh()


async def serve(engine) -> None:
    slots = asyncio.Semaphore(MAX_CONCURRENT_SELLERS)
    failures: dict[str, dict[int, int]] = {}

    async def check_bounded(owner: str) -> None:
        async with slots:
            try:
                await check_owner(owner, failures.setdefault(owner, {}))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.error('Hosted sales check failed for one seller; continuing with the others')

    while True:
        try:
            owners = await asyncio.to_thread(sales_owners, engine)
        except SQLAlchemyError:
            log.warning('Hosted sales schedule database is temporarily unavailable')
        else:
            for owner in failures.keys() - set(owners):
                del failures[owner]
            await asyncio.gather(*(check_bounded(owner) for owner in owners))
        await asyncio.sleep(INTERVAL_S)
