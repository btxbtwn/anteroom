"""Retain seven-day operator error impact independently of the SMTP outbox."""
from alembic import op
import sqlalchemy as sa

revision = '8afbfbc151dd'
down_revision = 'f1b4c8d2e930'
branch_labels = None
depends_on = None


def upgrade() -> None:
    db = op.get_bind()
    table = 'error_email_occurrences'
    if not sa.inspect(db).has_table(table):
        op.create_table(table,
                        sa.Column('id', sa.Integer, primary_key=True),
                        sa.Column('fingerprint', sa.String, nullable=False),
                        sa.Column('occurred_at', sa.Float, nullable=False),
                        sa.Column('seller_digest', sa.String(64)),
                        sa.Column('release', sa.String(255)), sa.Column('stack', sa.String))
        op.create_index('ix_error_email_occurrences_group_time', table, ['fingerprint', 'occurred_at', 'id'])
        op.create_index('ix_error_email_occurrences_occurred_at', table, ['occurred_at'])
    if db.dialect.name == 'postgresql':
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        # Default privileges can include direct grants (including to roles
        # with BYPASSRLS). Clear them before granting the writer its minimum.
        for role in ('PUBLIC', 'anteroom_seller', 'anon', 'authenticated', 'service_role', 'anteroom_api'):
            if role == 'PUBLIC' or db.scalar(sa.text('SELECT 1 FROM pg_roles WHERE rolname=:role'), {'role': role}):
                op.execute(f'REVOKE ALL ON {table} FROM {role}')
                op.execute(f'REVOKE ALL ON SEQUENCE {table}_id_seq FROM {role}')
        if db.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='anteroom_api'")):
            op.execute(f'GRANT SELECT, INSERT, DELETE ON {table} TO anteroom_api')
            op.execute(f'GRANT USAGE ON SEQUENCE {table}_id_seq TO anteroom_api')
            op.execute(f'CREATE POLICY operator_email ON {table} TO anteroom_api USING (true) WITH CHECK (true)')


def downgrade() -> None:
    op.drop_table('error_email_occurrences')
