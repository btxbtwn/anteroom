"""Find seller IDs for the hosted sales worker, without exposing credentials.

Revision ID: 9a46c0f2d381
Revises: 1e7ce78bb995
"""
from alembic import op
import sqlalchemy as sa

revision = '9a46c0f2d381'
down_revision = '1e7ce78bb995'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return
    op.execute("""
        CREATE FUNCTION anteroom_private.sales_detection_owners()
        RETURNS TABLE(owner_id text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path='' AS $$
            SELECT DISTINCT reference.user_id::text FROM public.user_secret_references AS reference
            WHERE reference.account = 'marketplace-accounts'
        $$;
        REVOKE ALL ON FUNCTION anteroom_private.sales_detection_owners() FROM PUBLIC, anteroom_seller;
    """)
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='anteroom_api'")):
        op.execute('GRANT EXECUTE ON FUNCTION anteroom_private.sales_detection_owners() TO anteroom_api')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP FUNCTION anteroom_private.sales_detection_owners()')
