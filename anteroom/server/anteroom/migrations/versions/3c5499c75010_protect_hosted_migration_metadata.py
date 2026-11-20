"""Protect migration metadata in Supabase's exposed public schema.

Revision ID: 3c5499c75010
Revises: b295f60b1610
"""
from alembic import op

revision = '3c5499c75010'
down_revision = 'b295f60b1610'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        # Alembic's owner can still read and advance the revision; clients and
        # runtime roles must not read or modify migration bookkeeping. No
        # permissive policy is installed for this internal table.
        op.execute('ALTER TABLE alembic_version ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE alembic_version DISABLE ROW LEVEL SECURITY')
