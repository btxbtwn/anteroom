"""Operator outbox: one control lock, targeted group reads, no SMTP transaction.

The JSON mirror and group_sync_version support running pre-migration workers.
Remove the mirror/legacy-writer trigger after 2026-12-15 once all running hosted
workers use this revision. It costs database-side rewrites, never a backlog read
by the new client. See docs/error-email-storage.md for the rolling protocol.
"""
import copy
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import JSON, Table, bindparam, cast as sql_cast, delete, func, select, type_coerce, update
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by

from anteroom.models.error_email import OperatorEmailGroup, OperatorEmailState

LIFECYCLE_ACTIVE = 'issue_lifecycle_active'


def report_release(report):
    release = (report.get('browser') or report['environment']).get('release')
    return release.replace('\x00', '�')[:255] if release and release != '0.0.0' else None


def _metadata(table, postgres):
    payload = table.c.payload
    value = (sql_cast(payload, JSONB).op('-')('groups').op('-')('last_sent') if postgres
             else func.json_remove(payload, '$.groups', '$.last_sent'))
    return type_coerce(value, JSON)


class EmailTransaction:
    def __init__(self, connection, table, groups, state):
        self.connection, self.table, self.groups, self.state = connection, table, groups, state
        self.original = copy.deepcopy(state)
        self.dirty = False

    def get_group(self, key):
        row = self.connection.execute(select(self.groups.c.payload, self.groups.c.last_sent).where(
            self.groups.c.fingerprint == key)).first()
        return (row.payload, row.last_sent) if row else (None, None)

    def get_issue(self, key):
        groups = self.groups
        return self.connection.execute(select(
            groups.c.status, groups.c.muted_until, groups.c.resolved_release,
            groups.c.occurrence_count, groups.c.first_seen, groups.c.last_seen,
            groups.c.payload, groups.c.last_sent,
        ).where(groups.c.fingerprint == key)).first()

    def record_issue(self, key, **values):
        self.connection.execute(update(self.groups).where(self.groups.c.fingerprint == key).values(**values))

    def seed_pending_issues(self):
        groups = self.groups
        pending = select(groups.c.id, groups.c.payload, groups.c.count).where(
            groups.c.payload.is_not(None), groups.c.last_seen.is_(None)).order_by(groups.c.id).limit(500)
        fields = ('headline', 'first_seen', 'last_seen', 'last_release', 'occurrence_count')
        backfill = update(groups).where(groups.c.id == bindparam('group_id')).values(
            {field: bindparam(field) for field in fields})
        # Read the final pre-activation queue after old writers have drained.
        last_id = 0
        while rows := self.connection.execute(pending.where(groups.c.id > last_id)).all():
            batch = []
            for group_id, group, count in rows:
                report = group['report']
                batch.append(dict(group_id=group_id, headline=report['title'].replace('\x00', '�'),
                                  first_seen=datetime.fromisoformat(group['first']).timestamp(),
                                  last_seen=datetime.fromisoformat(report['occurred_at']).timestamp(),
                                  last_release=report_release(report), occurrence_count=count))
            self.connection.execute(backfill, batch)
            last_id = rows[-1].id

    def put_group(self, key, group, last_sent, **issue):
        groups = self.groups
        values = {'payload': group, 'last_sent': last_sent, 'count': group['count'] if group else 0,
                  'due': group['due'] if group else None,
                  'warning': key.startswith('WARNING:'), **issue}
        existing = self.connection.execute(select(groups.c.id, groups.c.due).where(
            groups.c.fingerprint == key)).first()
        # A re-enqueued fingerprint joins the end of the pending queue.
        if existing and existing.due is None and group is not None:
            old = self.connection.execute(delete(groups).where(groups.c.id == existing.id).returning(
                groups.c.status, groups.c.resolved_at, groups.c.resolved_release, groups.c.muted_until,
                groups.c.note, groups.c.last_release, groups.c.occurrence_count, groups.c.headline,
                groups.c.first_seen, groups.c.last_seen)).one()
            values = {**old._mapping, **values}
            existing = None
        if existing:
            self.connection.execute(update(self.groups).where(self.groups.c.id == existing.id).values(**values))
        else:
            self.connection.execute(self.groups.insert().values(fingerprint=key, **values))
        self.dirty = True

    def due_groups(self, now, *, warning, limit, first_alert=False, sent_before=None):
        query = select(self.groups.c.fingerprint, self.groups.c.payload).where(
            self.groups.c.status == 'open', self.groups.c.warning == warning, self.groups.c.due <= now)
        if first_alert:
            query = query.where(self.groups.c.last_sent.is_(None))
        if sent_before is not None:
            query = query.where(self.groups.c.last_sent <= sent_before)
        rows = self.connection.execute(query.order_by(self.groups.c.id).limit(limit))
        return {key: group for key, group in rows}

    def pending_group_count(self) -> int:
        return int(self.connection.scalar(
            select(func.count()).select_from(self.groups).where(self.groups.c.payload.is_not(None)),
        ) or 0)

    def evict_oldest_warning(self, *, exclude=None) -> int:
        """Drop the oldest pending warning payload to make room for an error; preserve triage history."""
        groups = self.groups
        query = (
            select(groups.c.id, groups.c.count, groups.c.last_seen, groups.c.last_sent)
            .where(groups.c.payload.is_not(None), groups.c.warning.is_(True))
        )
        if exclude:
            query = query.where(groups.c.fingerprint.notin_(exclude))
        victim = self.connection.execute(query.order_by(groups.c.id).limit(1)).first()
        if victim is None:
            return 0
        if victim.last_seen is None and victim.last_sent is None:
            self.connection.execute(delete(groups).where(groups.c.id == victim.id))
        else:
            self.connection.execute(
                update(groups).where(groups.c.id == victim.id).values(payload=None, due=None, count=0),
            )
        self.dirty = True
        return max(1, int(victim.count or 0))

    def expire_sent(self, before):
        # Pending repeats must not turn into first alerts after a long pause.
        # Only completed fingerprints need their bounded history removed.
        expired = (self.groups.c.last_sent <= before) & self.groups.c.payload.is_(None)
        forgotten = self.connection.execute(delete(self.groups).where(expired, self.groups.c.last_seen.is_(None)))
        cleared = self.connection.execute(update(self.groups).where(expired, self.groups.c.last_seen.is_not(None))
                                          .values(last_sent=None))
        self.dirty = self.dirty or bool(forgotten.rowcount or cleared.rowcount)

    def finish(self):
        if self.state == self.original and not self.dirty:
            return
        postgres = self.connection.dialect.name == 'postgresql'
        # Transitional mirror is assembled inside the database. No reports
        # cross the connection here; the normalized rows are already written.
        # JSONB would sort fingerprint keys and change the queue seen by old
        # senders. Keep the compatibility object ordered by enqueue id.
        ordered = select(self.groups).order_by(self.groups.c.id).subquery()
        if postgres:
            pending = select(func.json_object_agg(self.groups.c.fingerprint, aggregate_order_by(
                self.groups.c.payload, self.groups.c.id))).where(self.groups.c.payload.is_not(None)).scalar_subquery()
            sent = select(func.json_object_agg(self.groups.c.fingerprint, self.groups.c.last_sent)).where(
                self.groups.c.last_sent.is_not(None)).scalar_subquery()
            metadata = [argument for key, item in self.state.items() for argument in (key, sql_cast(item, JSON))]
            value = func.json_build_object(*metadata,
                'groups', func.coalesce(pending, sql_cast({}, JSON)),
                'last_sent', func.coalesce(sent, sql_cast({}, JSON)))
        else:
            pending = select(func.json_group_object(ordered.c.fingerprint, func.json(ordered.c.payload))).where(
                ordered.c.payload.is_not(None)).scalar_subquery()
            sent = select(func.json_group_object(ordered.c.fingerprint, ordered.c.last_sent)).where(
                ordered.c.last_sent.is_not(None)).scalar_subquery()
            value = func.json_set(func.json(json.dumps(self.state)), '$.groups', func.json(pending),
                                  '$.last_sent', func.json(sent))
        self.connection.execute(update(self.table).where(self.table.c.id == 1).values(
            payload=sql_cast(value, JSON) if postgres else type_coerce(value, JSON),
            group_sync_version=self.table.c.group_sync_version + 1))


class EmailStore:
    def __init__(self, engine):
        self.engine = engine
        self.table = cast(Table, OperatorEmailState.__table__)
        self.groups = cast(Table, OperatorEmailGroup.__table__)

    @contextmanager
    def transaction(self):
        with self.engine.connect() as connection:
            if self.engine.dialect.name == 'sqlite':
                connection.exec_driver_sql('BEGIN IMMEDIATE')
            state = connection.execute(select(_metadata(self.table, connection.dialect.name == 'postgresql'))
                                       .where(self.table.c.id == 1).with_for_update()).scalar_one()
            transaction = EmailTransaction(connection, self.table, self.groups, state)
            yield transaction
            transaction.finish()
            connection.commit()

    def summary(self):
        # One statement gives a consistent metadata/count snapshot without reports.
        pending = select(func.coalesce(func.sum(self.groups.c.count), 0)).scalar_subquery()
        with self.engine.connect() as connection:
            state, count = connection.execute(select(
                _metadata(self.table, connection.dialect.name == 'postgresql'), pending).where(self.table.c.id == 1)).one()
            return state, int(count)

    def lifecycle_active(self) -> bool:
        with self.engine.connect() as connection:
            return bool(connection.scalar(select(
                self.table.c.payload[LIFECYCLE_ACTIVE].as_boolean()).where(self.table.c.id == 1)))

    def list_groups(self, status, *, severity=None, sort='last_seen', limit=100, offset=0):
        groups = self.groups
        query = select(groups.c.fingerprint, groups.c.headline, groups.c.occurrence_count.label('count'),
                       groups.c.first_seen, groups.c.last_seen, groups.c.last_release,
                       groups.c.status, groups.c.resolved_release, groups.c.muted_until, groups.c.note).where(
            groups.c.status == status, groups.c.last_seen.is_not(None))
        if severity is not None:
            query = query.where(groups.c.fingerprint.startswith(severity + ':', autoescape=True))
        if sort == 'count':
            query = query.order_by(groups.c.occurrence_count.desc())
        query = query.order_by(groups.c.last_seen.desc(), groups.c.fingerprint).limit(limit).offset(offset)
        with self.engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        results = []
        for row in rows:
            issue = dict(row)
            issue['severity'], issue['fingerprint'] = issue['fingerprint'].split(':', 1)
            for field in ('first_seen', 'last_seen', 'muted_until'):
                if issue[field] is not None:
                    issue[field] = datetime.fromtimestamp(issue[field], UTC)
            results.append(issue)
        return results

    def triage(self, severity, fingerprint, **fields):
        groups = self.groups
        with self.transaction() as transaction:
            updated = transaction.connection.execute(update(groups).where(
                groups.c.fingerprint == f'{severity}:{fingerprint}', groups.c.last_seen.is_not(None)
            ).values(resolved_at=None, resolved_release=None, muted_until=None).values(**fields))
            return updated.rowcount == 1

    def read(self):
        """Full compatibility snapshot for migration checks; hot paths use summary."""
        with self.engine.connect() as connection:
            return connection.execute(select(self.table.c.payload).where(self.table.c.id == 1)).scalar_one()
