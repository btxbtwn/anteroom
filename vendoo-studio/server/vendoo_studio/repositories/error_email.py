"""Operator-only outbox. Transactions serialize budget reservations, never SMTP.

The hosted engine uses the API role, outside every seller's session. Sellers
cannot access this table. SQLite provides the same boundary in tests.
"""
from contextlib import contextmanager
from typing import cast

from sqlalchemy import Table, select, update

from vendoo_studio.models.error_email import OperatorEmailState


class EmailStore:
    def __init__(self, engine):
        self.engine = engine
        self.table = cast(Table, OperatorEmailState.__table__)

    @contextmanager
    def transaction(self):
        with self.engine.connect() as connection:
            if self.engine.dialect.name == 'sqlite':
                connection.exec_driver_sql('BEGIN IMMEDIATE')
            row = connection.execute(select(self.table.c.payload).where(self.table.c.id == 1).with_for_update()).one()
            state = row[0]
            yield state
            connection.execute(update(self.table).where(self.table.c.id == 1).values(payload=state))
            connection.commit()

    def read(self):
        with self.engine.connect() as connection:
            return connection.execute(select(self.table.c.payload).where(self.table.c.id == 1)).scalar_one()
