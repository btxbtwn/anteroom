"""Offer evidence, not schedule executions or inferred sales attribution."""
from datetime import UTC, date, datetime, time, timedelta
from typing import TypedDict
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from anteroom.models.ownership import LOCAL_USER_ID
from anteroom.repositories.offer_analytics import ebay_counts, ebay_offer_log, promotion_counts, promotion_offer_log
from anteroom.services.service_context import current


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
    for market, state, evidence, has_receipt, count, recipients in promotion_counts(db, seller, lower, upper):
        if market not in rows:
            continue
        row = rows[market]
        if state == 'done':
            if market == 'mercari' and evidence == 'recipients_created' and has_receipt:
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
    for state, has_receipt, count, recipients in ebay_counts(db, seller, lower, upper):
        row = rows['ebay']
        if state == 'sent' and has_receipt:
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


# One word per outcome, the same for every marketplace's journal.
LOG_STATUS = {'done': 'sent', 'sent': 'sent', 'sending': 'sending', 'unknown': 'unconfirmed',
              'checked': 'checked', 'failed': 'failed', 'skipped': 'failed'}


def _cursor(value: str | None) -> tuple[datetime, str] | None:
    if value is None:
        return None
    at, sep, row_id = value.partition('|')
    if not sep or not row_id:
        raise ValueError('Invalid cursor')
    return datetime.fromisoformat(at), row_id


def _iso(value: datetime) -> str:
    return value.isoformat() + 'Z'


def offer_log(db: Session, *, limit: int = 50, before: str | None = None) -> dict:
    """Every offer that reached a marketplace, newest first, across all marketplaces.

    Each entry keeps only what the marketplace confirmed: a recipient count only
    where it reported one, never one inferred from likes.
    """
    after = _cursor(before)
    context = current()
    seller = context.seller_id if context else LOCAL_USER_ID
    entries = [dict(id=row.id, marketplace=row.marketplace, title=row.title, listing_id=row.listing_id,
        sent_at=row.sent_at, status=LOG_STATUS.get(row.state, row.state), price_cents=row.price_cents,
        offer_price_cents=row.offer_price_cents,
        recipients=row.recipient_count if row.confirmation == 'recipients_created' and (row.recipient_count or 0) > 0 else None,
        confirmation=row.confirmation, like_count=row.like_count, note=row.note)
        for row in promotion_offer_log(db, seller, after, limit + 1)]
    entries += [dict(id=row.id, marketplace='ebay', title=title, listing_id=listing_id, sent_at=row.sent_at,
        status=LOG_STATUS.get(row.state, row.state), price_cents=row.current_price_cents,
        offer_price_cents=row.offer_price_cents,
        recipients=row.recipient_count if row.state == 'sent' and row.recipient_count > 0 else None,
        confirmation='recipients_created' if row.state == 'sent' and row.recipient_count > 0 else None,
        like_count=None, note=row.note)
        for row, listing_id, title in ebay_offer_log(db, seller, after, limit + 1)]
    entries.sort(key=lambda entry: (entry['sent_at'], entry['id']), reverse=True)
    page = entries[:limit]
    more = len(entries) > limit
    next_before = f"{page[-1]['sent_at'].isoformat()}|{page[-1]['id']}" if more and page else None
    for entry in page:
        entry['sent_at'] = _iso(entry['sent_at'])
    return dict(entries=page, next_before=next_before)
