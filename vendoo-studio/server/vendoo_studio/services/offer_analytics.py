"""Offer evidence, not schedule executions or inferred sales attribution."""
from datetime import UTC, date, datetime, time, timedelta
from typing import TypedDict
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from vendoo_studio.models.ownership import LOCAL_USER_ID
from vendoo_studio.repositories.offer_analytics import ebay_counts, promotion_counts
from vendoo_studio.services.service_context import current


class OfferCounts(TypedDict):
    marketplace: str
    confirmed_recipients: int | None
    confirmed_sends: int
    accepted_requests: int
    completed_jobs: int
    unverified: int
    unknown: int
    failed: int
    pending: int


def period_bounds(start: date, end: date, timezone: str) -> tuple[datetime, datetime]:
    if start > end or (end - start).days > 365:
        raise ValueError('Choose a period of at most 366 days, with the start on or before the end.')
    zone = ZoneInfo(timezone)
    def midnight(day: date) -> datetime:
        return datetime.combine(day, time.min, zone).astimezone(UTC).replace(tzinfo=None)
    return midnight(start), midnight(end + timedelta(days=1))


def offer_analytics(db: Session, *, start: date, end: date, timezone: str) -> dict:
    lower, upper = period_bounds(start, end, timezone)
    context = current()
    seller = context.seller_id if context else LOCAL_USER_ID
    rows: dict[str, OfferCounts] = {market: OfferCounts(marketplace=market, confirmed_recipients=None, confirmed_sends=0,
        accepted_requests=0, completed_jobs=0, unverified=0, unknown=0, failed=0, pending=0)
        for market in ('ebay', 'poshmark', 'mercari', 'depop')}
    for market, state, evidence, count, recipients in promotion_counts(db, seller, lower, upper):
        if market not in rows:
            continue
        row = rows[market]
        if state == 'done':
            if market == 'mercari' and evidence == 'recipients_created' and recipients and recipients > 0:
                row['confirmed_recipients'] = (row['confirmed_recipients'] or 0) + recipients
                row['confirmed_sends'] += count
            elif market == 'poshmark' and evidence == 'request_accepted':
                row['accepted_requests'] += count
            elif market == 'depop' and evidence == 'job_completed':
                row['completed_jobs'] += count
            else:
                row['unverified'] += count
        elif state in ('unknown', 'checked'):
            row['unknown'] += count
        elif state == 'failed':
            row['failed'] += count
        elif state == 'sending':
            row['pending'] += count
    for state, count, recipients in ebay_counts(db, seller, lower, upper):
        row = rows['ebay']
        if state == 'sent' and recipients and recipients > 0:
            row['confirmed_recipients'] = (row['confirmed_recipients'] or 0) + recipients
            row['confirmed_sends'] += count
        elif state in ('unknown', 'checked'):
            row['unknown'] += count
        elif state == 'failed':
            row['failed'] += count
        elif state == 'sending':
            row['pending'] += count
        elif state == 'sent':
            row['unverified'] += count
    return dict(start=start.isoformat(), end=end.isoformat(), timezone=timezone,
                confirmed_conversions=None, marketplaces=list(rows.values()))
