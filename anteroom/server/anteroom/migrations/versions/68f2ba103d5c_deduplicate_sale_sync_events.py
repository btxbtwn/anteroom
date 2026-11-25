"""Keep one tracker outcome per seller, sale, marketplace and action.

Revision ID: 68f2ba103d5c
Revises: c7a3e9f41b28
Create Date: 2026-11-25 06:27:12.489408
"""
from __future__ import annotations

from alembic import op


revision = '68f2ba103d5c'
down_revision = 'c7a3e9f41b28'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_context().dialect.name == 'postgresql':
        # Exclude writers between cleanup and index creation. Reads can continue.
        op.execute('LOCK TABLE sale_sync_events IN SHARE ROW EXCLUSIVE MODE')
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
    op.create_index(
        'uq_sale_sync_events_seller_sale_target_action', 'sale_sync_events',
        ['user_id', 'sale_id', 'target_marketplace', 'action'], unique=True,
    )


def downgrade() -> None:
    op.drop_index('uq_sale_sync_events_seller_sale_target_action', table_name='sale_sync_events')
