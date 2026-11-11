"""Persist the operator email outbox and rolling budget.

Revision ID: b7e912ca4801
Revises: a12a51c75c22
"""
from alembic import op
import sqlalchemy as sa

revision = 'b7e912ca4801'
down_revision = 'a12a51c75c22'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Legacy adoption creates current metadata before replaying revisions.
    if not sa.inspect(op.get_bind()).has_table('error_email_state'):
        table = op.create_table('error_email_state',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('payload', sa.JSON(), nullable=False))
    else:
        table = sa.Table('error_email_state', sa.MetaData(), autoload_with=op.get_bind())
    if not op.get_bind().scalar(sa.select(table.c.id).where(table.c.id == 1)):
        op.bulk_insert(table, [{'id': 1, 'payload': {}}])
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE error_email_state ENABLE ROW LEVEL SECURITY')
        op.execute('REVOKE ALL ON error_email_state FROM PUBLIC, anteroom_seller')
        for role in ('anon', 'authenticated'):
            if op.get_bind().scalar(sa.text('SELECT 1 FROM pg_roles WHERE rolname=:role'), {'role': role}):
                op.execute(f'REVOKE ALL ON error_email_state FROM {role}')
        if op.get_bind().scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='anteroom_api'")):
            op.execute('GRANT SELECT, UPDATE ON error_email_state TO anteroom_api')
            op.execute('CREATE POLICY operator_email ON error_email_state TO anteroom_api USING (true) WITH CHECK (true)')


def downgrade() -> None:
    op.drop_table('error_email_state')
