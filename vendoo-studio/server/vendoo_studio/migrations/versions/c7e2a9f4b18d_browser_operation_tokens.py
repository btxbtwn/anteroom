"""Web Save draft operations that the seller's Chrome runs with a single-use token

Revision ID: c7e2a9f4b18d
Revises: a6f3d1c8e925
Create Date: 2026-11-01 18:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'c7e2a9f4b18d'
down_revision = 'a6f3d1c8e925'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('jobs') as batch:
        batch.add_column(sa.Column('operation_token_hash', sa.String(length=64), nullable=True))
        batch.add_column(sa.Column('operation_expires_at', sa.DateTime(), nullable=True))
        batch.create_index('ix_jobs_operation_token_hash', ['operation_token_hash'], unique=True)
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return
    # The extension fetches photos and reports the result with the operation's
    # token, not a seller session. This returns the owner and job for a token
    # hash that is still issued and running, nothing else; the route then enters
    # that seller's own row-secured context. Seller sessions cannot call it.
    op.execute("""
        CREATE FUNCTION anteroom_private.browser_operation_owner(token_hash text)
        RETURNS TABLE(owner_id text, job_id text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path='' AS $$
            SELECT job.user_id::text, job.id FROM public.jobs AS job
                WHERE job.operation_token_hash = token_hash AND job.status = 'dispatched'
        $$;
        REVOKE ALL ON FUNCTION anteroom_private.browser_operation_owner(text) FROM PUBLIC, anteroom_seller;
    """)
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='anteroom_api'")):
        op.execute('GRANT EXECUTE ON FUNCTION anteroom_private.browser_operation_owner(text) TO anteroom_api')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP FUNCTION anteroom_private.browser_operation_owner(text)')
    with op.batch_alter_table('jobs') as batch:
        batch.drop_index('ix_jobs_operation_token_hash')
        batch.drop_column('operation_expires_at')
        batch.drop_column('operation_token_hash')
