"""Operator outbox: one control lock, targeted group reads, no SMTP transaction.

The JSON mirror and group_sync_version support running pre-migration workers.
Remove the mirror/legacy-writer trigger after 2026-12-15 once all running hosted
workers use this revision. It costs database-side rewrites, never a backlog read
by the new client. See docs/error-email-storage.md for the rolling protocol.
"""
import copy
import json
from contextlib import contextmanager
from typing import cast

from sqlalchemy import JSON, Table, cast as sql_cast, delete, func, select, type_coerce, update
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by

from vendoo_studio.models.error_email import OperatorEmailGroup, OperatorEmailState


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

    def put_group(self, key, group, last_sent):
        values = {'payload': group, 'last_sent': last_sent, 'count': group['count'] if group else 0,
                  'due': group['due'] if group else None,
                  'warning': group['report']['severity'] == 'WARNING' if group else False}
        existing = self.connection.execute(select(self.groups.c.id, self.groups.c.due).where(
            self.groups.c.fingerprint == key)).first()
        # A re-enqueued fingerprint joins the end of the pending queue.
        if existing and existing.due is None and group is not None:
            self.connection.execute(delete(self.groups).where(self.groups.c.id == existing.id))
            existing = None
        if existing:
            self.connection.execute(update(self.groups).where(self.groups.c.id == existing.id).values(**values))
        else:
            self.connection.execute(self.groups.insert().values(fingerprint=key, **values))
        self.dirty = True

    def due_groups(self, now, *, warning, limit):
        rows = self.connection.execute(select(self.groups.c.fingerprint, self.groups.c.payload).where(
            self.groups.c.warning == warning, self.groups.c.due <= now).order_by(self.groups.c.id).limit(limit))
        return {key: group for key, group in rows}

    def expire_sent(self, before):
        expired = self.groups.c.last_sent <= before
        keys = self.connection.scalars(select(self.groups.c.fingerprint).where(expired)).all()
        if not keys:
            return
        self.connection.execute(delete(self.groups).where(expired, self.groups.c.payload.is_(None)))
        self.connection.execute(update(self.groups).where(expired).values(last_sent=None))
        self.dirty = True

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

    def read(self):
        """Full compatibility snapshot for migration checks; hot paths use summary."""
        with self.engine.connect() as connection:
            return connection.execute(select(self.table.c.payload).where(self.table.c.id == 1)).scalar_one()
