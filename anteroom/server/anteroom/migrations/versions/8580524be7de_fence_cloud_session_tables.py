"""Fence the cloud session tables against writes during account deletion

Revision ID: 8580524be7de
Revises: ae814be47ba5
Create Date: 2026-11-22 05:00:00.000000
"""
from __future__ import annotations

from alembic import op


revision = '8580524be7de'
down_revision = 'ae814be47ba5'
branch_labels = None
depends_on = None

# c92e35f8b041 attached account_write_guard to the seller tables that existed
# then. a3c9e7d12f48 added these later without it, so a seller session still
# valid during or after account deletion could write their rows back.
TABLES = ('cloud_marketplace_sessions', 'cloud_session_audit', 'cloud_operation_journals')


def upgrade() -> None:
    if op.get_bind().dialect.name != 'postgresql':
        return
    for table in TABLES:
        op.execute(f'DROP TRIGGER IF EXISTS account_write_guard ON public."{table}"')
        op.execute(f'CREATE TRIGGER account_write_guard BEFORE INSERT OR UPDATE ON public."{table}" '
                   'FOR EACH ROW EXECUTE FUNCTION anteroom_private.guard_account_write()')


def downgrade() -> None:
    if op.get_bind().dialect.name != 'postgresql':
        return
    # Preserve the original fence-removal rule. Block receipt changes until
    # the trigger drops commit so a deletion cannot race the emptiness check.
    op.execute('LOCK TABLE anteroom_private.account_cleanup IN SHARE MODE')
    op.execute('''DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM anteroom_private.account_cleanup) THEN
            RAISE EXCEPTION 'Deletion fences cannot be removed while deleted or pending accounts exist';
        END IF;
    END $$''')
    for table in TABLES:
        op.execute(f'DROP TRIGGER IF EXISTS account_write_guard ON public."{table}"')
