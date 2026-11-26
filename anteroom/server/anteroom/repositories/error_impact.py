"""Rolling operator counts. Keep only code locations and fingerprint-local seller digests."""
from datetime import datetime
from hashlib import sha256
import re
from typing import cast

from sqlalchemy import Table, func, select

from anteroom.models.error_email import OperatorEmailOccurrence

WEEK = 7 * 86400
occurrences = cast(Table, OperatorEmailOccurrence.__table__)
_PYTHON = re.compile(r'^\s*File "[^"\n]*/([A-Za-z_][\w]*\.py)", line (\d{1,8}), in ([\w<>]+)$', re.M)
_BROWSER = re.compile(r'/assets/([\w.-]{1,96}\.js):(\d{1,8}):(\d{1,8})(?:\)|\s|$)')


def _stack(report):
    stack = report.get('stack') or ''
    if report.get('browser'):
        frames = [f'{file}:{line}:{column}' for file, line, column in _BROWSER.findall(stack)]
    else:
        frames = [f'{file}:{line} in {name[:96]}' for file, line, name in _PYTHON.findall(stack)]
    return '\n'.join(frames[-8:]) or None


def record_batch(connection, reports, release_of, now):
    rows = []
    for report in reports:
        occurred = datetime.fromisoformat(report['occurred_at']).timestamp()
        if occurred < now - WEEK:
            continue
        key = report['severity'] + ':' + report['fingerprint']
        seller = (report.get('request') or {}).get('seller_id')
        rows.append(dict(fingerprint=key, occurred_at=occurred, release=release_of(report),
                         seller_digest=sha256(f'{key}:{seller}'.encode()).hexdigest() if seller else None,
                         stack=_stack(report)))
    if rows:
        connection.execute(occurrences.insert(), rows)


def summary(connection, keys, now):
    c = occurrences.c
    within = c.occurred_at.between(now - WEEK, now)
    totals = select(c.fingerprint, func.count().label('occurrences'),
                    func.count(c.seller_digest.distinct()).label('affected_sellers'),
                    (func.count() - func.count(c.seller_digest)).label('unattributed')).where(
                        c.fingerprint.in_(keys), within).group_by(c.fingerprint).subquery()

    def sample(column, *, earliest=False, require_value=False):
        query = select(column).where(c.fingerprint == totals.c.fingerprint, within)
        if require_value:
            query = query.where(column.is_not(None))
        order = (c.occurred_at, c.id) if earliest else (c.occurred_at.desc(), c.id.desc())
        return query.order_by(*order).limit(1).scalar_subquery()

    query = select(totals, sample(c.release, earliest=True).label('first_release'),
                   sample(c.release).label('last_release'), sample(c.stack, require_value=True).label('stack'))
    result = {}
    for row in connection.execute(query).mappings():
        impact = dict(row)
        result[impact.pop('fingerprint')] = impact
    return result
