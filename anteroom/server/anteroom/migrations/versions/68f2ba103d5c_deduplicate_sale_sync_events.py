"""Keep one tracker outcome per seller, sale, marketplace and action.

Revision ID: 68f2ba103d5c
Revises: c7a3e9f41b28
Create Date: 2026-11-25 06:27:12.489408
"""
from __future__ import annotations

import sys
import time
from contextlib import nullcontext

from alembic import op
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError


revision = '68f2ba103d5c'
down_revision = 'c7a3e9f41b28'
branch_labels = None
depends_on = None
INDEX_NAME = 'uq_sale_sync_events_seller_sale_target_action'
LOCK_RETRY_DELAYS = (10, 20, 40)


def _deduplicate() -> None:
    # DELETE starts the SQLite write transaction. Preserve confirmed results
    # first, then in-flight jobs that can still be reconciled. Rank ties by age
    # so a duplicate never wins just because it has a newer listing snapshot.
    op.execute("""
        DELETE FROM sale_sync_events
        WHERE id IN (
            SELECT id FROM (
                SELECT id, row_number() OVER (
                    PARTITION BY user_id, sale_id, target_marketplace, action
                    ORDER BY CASE
                        WHEN status = 'succeeded' THEN 0
                        WHEN status IN ('pending', 'retrying') AND native_job_id IS NOT NULL THEN 1
                        WHEN status IN ('pending', 'retrying') THEN 2
                        WHEN status = 'failed' THEN 3
                        ELSE 4
                    END, created_at, id
                ) AS position
                FROM sale_sync_events
            ) AS ranked
            WHERE position > 1
        )
    """)


def _deduplicate_postgres(connection) -> None:
    # Fence writers only while choosing the rows to keep. The concurrent
    # index build runs after this short transaction releases its locks.
    # Alembic has already committed before this autocommit block, so env.py's
    # lock-timeout retry no longer applies: retry the lock here instead. Each
    # attempt is its own transaction, so repeating it is safe.
    for delay in (*LOCK_RETRY_DELAYS, None):
        connection.exec_driver_sql('BEGIN')
        try:
            connection.exec_driver_sql('LOCK TABLE sale_sync_events IN SHARE ROW EXCLUSIVE MODE')
            _deduplicate()
        except DBAPIError as error:
            connection.exec_driver_sql('ROLLBACK')
            if delay is None or getattr(error.orig, 'sqlstate', None) != '55P03':
                raise
        except BaseException:
            connection.exec_driver_sql('ROLLBACK')
            raise
        else:
            connection.exec_driver_sql('COMMIT')
            return
        print(
            f'sale_sync_events is busy; retrying the duplicate cleanup in {delay}s.',
            file=sys.stderr, flush=True,
        )
        time.sleep(delay)


def upgrade() -> None:
    context = op.get_context()
    postgres = context.dialect.name == 'postgresql'
    if not postgres:
        _deduplicate()
    with context.autocommit_block() if postgres else nullcontext():
        if postgres:
            connection = op.get_bind()
            valid = connection.scalar(text(
                'SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass(:name)'
            ), {'name': INDEX_NAME})
            if valid is True:
                return  # A prior attempt built the index before Alembic marked the revision.
            if valid is False:
                op.drop_index(INDEX_NAME, table_name='sale_sync_events', postgresql_concurrently=True)
        for attempt in range(3):
            if postgres:
                _deduplicate_postgres(op.get_bind())
            try:
                op.create_index(
                    INDEX_NAME, 'sale_sync_events', ['user_id', 'sale_id', 'target_marketplace', 'action'],
                    unique=True, postgresql_concurrently=True, if_not_exists=not postgres,
                )
            except IntegrityError:
                if not postgres:
                    raise
                # An old writer can insert another duplicate while the index
                # builds. Remove the invalid index so cleanup can run again.
                op.drop_index(INDEX_NAME, table_name='sale_sync_events', if_exists=True, postgresql_concurrently=True)
                if attempt == 2:
                    raise
            else:
                return


def downgrade() -> None:
    context = op.get_context()
    postgres = context.dialect.name == 'postgresql'
    with context.autocommit_block() if postgres else nullcontext():
        op.drop_index(INDEX_NAME, table_name='sale_sync_events', postgresql_concurrently=True)
