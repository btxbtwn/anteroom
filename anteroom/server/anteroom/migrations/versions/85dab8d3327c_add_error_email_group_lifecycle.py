"""Add operator issue history and lifecycle to the error email outbox.

Revision ID: 85dab8d3327c
Revises: 4cf6b35245d2
"""
from contextlib import nullcontext

from alembic import op
import sqlalchemy as sa

revision = '85dab8d3327c'
down_revision = '4cf6b35245d2'
branch_labels = None
depends_on = None


def _normalize_legacy_json(connection) -> None:
    # Install the write fence after committing the column DDL. Existing
    # readers can continue while old writers are fenced out of the queue.
    connection.exec_driver_sql('BEGIN')
    try:
        connection.exec_driver_sql('LOCK TABLE error_email_state IN EXCLUSIVE MODE')
        connection.exec_driver_sql('LOCK TABLE error_email_groups IN SHARE ROW EXCLUSIVE MODE')
        connection.exec_driver_sql(r"""
            CREATE OR REPLACE FUNCTION anteroom_private.normalize_error_email_json() RETURNS trigger
            LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
            BEGIN
              NEW.payload := regexp_replace(NEW.payload::text,
                  $regex$(?<!\\)((?:\\\\)*)\\u0000$regex$, $sub$\1\\ufffd$sub$, 'g')::json;
              RETURN NEW;
            END $$;
        """)
        connection.exec_driver_sql('REVOKE ALL ON FUNCTION anteroom_private.normalize_error_email_json() FROM PUBLIC')
        for table in ('error_email_state', 'error_email_groups'):
            connection.exec_driver_sql(rf"""
                CREATE OR REPLACE TRIGGER normalize_error_email_json
                BEFORE INSERT OR UPDATE OF payload ON {table}
                FOR EACH ROW WHEN (strpos(NEW.payload::text, $nul$\u0000$nul$) > 0)
                EXECUTE FUNCTION anteroom_private.normalize_error_email_json()
            """)
        # Match escaped NUL, preserving literal text like "\\u0000".
        pattern = r'(?<!\\)((?:\\\\)*)\\u0000'
        if connection.scalar(sa.text(
            "SELECT payload::text ~ :pattern FROM error_email_state WHERE id=1"
        ), {'pattern': pattern}):
            connection.execute(sa.text("""
                UPDATE error_email_groups
                SET payload = payload
                WHERE payload::text ~ :pattern
            """), {'pattern': pattern})
            connection.execute(sa.text("""
                UPDATE error_email_state
                SET payload = payload, group_sync_version = group_sync_version + 1
                WHERE id=1
            """))
    except BaseException:
        connection.exec_driver_sql('ROLLBACK')
        raise
    else:
        connection.exec_driver_sql('COMMIT')


def upgrade() -> None:
    connection = op.get_bind()
    postgres = connection.dialect.name == 'postgresql'
    if not postgres:
        # sqlite3 only begins a real transaction on writes in legacy mode.
        connection.exec_driver_sql('UPDATE error_email_state SET id=id WHERE id=1')
    # SQLite adoption can create these columns and indexes from ORM metadata.
    inspector = sa.inspect(connection)
    existing = {column['name'] for column in inspector.get_columns('error_email_groups')}
    indexes = {index['name'] for index in inspector.get_indexes('error_email_groups')}
    if 'status' not in existing:
        op.execute("ALTER TABLE error_email_groups ADD COLUMN status VARCHAR DEFAULT 'open' NOT NULL "
                   "CONSTRAINT ck_error_email_group_status CHECK (status IN ('open', 'resolved', 'muted'))")
    columns = (
        sa.Column('resolved_at', sa.Float()),
        sa.Column('resolved_release', sa.String(255)),
        sa.Column('muted_until', sa.Float()),
        sa.Column('last_release', sa.String(255)),
        sa.Column('note', sa.String(500)),
        sa.Column('occurrence_count', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('headline', sa.String()),
        sa.Column('first_seen', sa.Float()),
        sa.Column('last_seen', sa.Float()),
    )
    for column in columns:
        if column.name not in existing:
            op.add_column('error_email_groups', column)

    with op.get_context().autocommit_block() if postgres else nullcontext():
        if postgres:
            _normalize_legacy_json(connection)
        names = {'ix_error_email_groups_status_last_seen': ['status', 'last_seen'],
                 'ix_error_email_groups_completed': ['last_sent']}
        for name, keys in names.items():
            if postgres:
                valid = connection.scalar(sa.text(
                    'SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass(:name)'
                ), {'name': name})
                if valid is True:
                    continue
                if valid is False:
                    op.drop_index(name, table_name='error_email_groups', postgresql_concurrently=True)
            elif name in indexes:
                continue
            predicate = sa.text('last_sent IS NOT NULL AND payload IS NULL') if keys == ['last_sent'] else None
            op.create_index(name, 'error_email_groups', keys, postgresql_concurrently=True,
                            postgresql_where=predicate, sqlite_where=predicate)


def downgrade() -> None:
    postgres = op.get_bind().dialect.name == 'postgresql'
    with op.get_context().autocommit_block() if postgres else nullcontext():
        op.drop_index('ix_error_email_groups_completed', table_name='error_email_groups',
                      if_exists=postgres, postgresql_concurrently=True)
        op.drop_index('ix_error_email_groups_status_last_seen', table_name='error_email_groups',
                      if_exists=postgres, postgresql_concurrently=True)
    if postgres:
        # Match the control-row-first lock order of a concurrent sender.
        for table in ('error_email_state', 'error_email_groups'):
            op.execute(f'DROP TRIGGER normalize_error_email_json ON {table}')
        op.execute('DROP FUNCTION anteroom_private.normalize_error_email_json()')
    for name in ('last_seen', 'first_seen', 'headline', 'occurrence_count', 'note',
                 'last_release', 'muted_until', 'resolved_release', 'resolved_at', 'status'):
        op.drop_column('error_email_groups', name)
