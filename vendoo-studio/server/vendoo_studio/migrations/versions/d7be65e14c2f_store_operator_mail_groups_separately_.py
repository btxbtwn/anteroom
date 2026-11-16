"""Store operator mail groups separately with rolling writer compatibility.

Revision ID: d7be65e14c2f
Revises: c3f1a9d27e64

The old JSON remains a transactional mirror until 2026-12-15; remove it and
these triggers only after every running worker uses per-group storage. Old
workers can reserve/send/finish across the migration without losing new adds.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = 'd7be65e14c2f'
down_revision = 'c3f1a9d27e64'
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == 'sqlite':
        # sqlite3 legacy mode does not BEGIN for DDL/SELECT. Acquire the
        # writer lock before the snapshot, through trigger installation.
        connection.exec_driver_sql('UPDATE error_email_state SET id=id WHERE id=1')
    inspector = sa.inspect(connection)
    if not inspector.has_table('error_email_groups'):
        op.create_table('error_email_groups',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('fingerprint', sa.String(), nullable=False, unique=True),
            sa.Column('payload', sa.JSON(none_as_null=True), nullable=True),
            sa.Column('count', sa.BigInteger(), nullable=False, server_default='0'),
            sa.Column('due', sa.Float(), nullable=True),
            sa.Column('warning', sa.Boolean(), nullable=False),
            sa.Column('last_sent', sa.Float(), nullable=True),
            sa.CheckConstraint('count >= 0', name='ck_error_email_group_count'))
        op.create_index('ix_error_email_groups_due', 'error_email_groups', ['warning', 'due'])
    if 'group_sync_version' not in {column['name'] for column in inspector.get_columns('error_email_state')}:
        op.add_column('error_email_state', sa.Column('group_sync_version', sa.Integer(), server_default='0', nullable=False))
    if connection.dialect.name == 'postgresql':
        # Also fences already-running old writers during the one-time backfill.
        op.execute('LOCK TABLE error_email_state IN ACCESS EXCLUSIVE MODE')
    state = sa.Table('error_email_state', sa.MetaData(), autoload_with=connection)
    groups = sa.Table('error_email_groups', sa.MetaData(), autoload_with=connection)
    payload = connection.scalar(sa.select(state.c.payload).where(state.c.id == 1)) or {}
    sent = payload.get('last_sent', {})
    # Batch pending rows in insertion order, then sent-only tombstones. Omitting
    # payload on tombstones yields SQL NULL even with reflected JSON's default.
    pending = payload.get('groups', {})
    rows = [{'fingerprint': key, 'payload': group, 'count': group['count'],
             'due': group['due'], 'warning': group['report']['severity'] == 'WARNING',
             'last_sent': sent.get(key)} for key, group in pending.items()]
    if rows:
        connection.execute(groups.insert(), rows)
    history = [{'fingerprint': key, 'count': 0, 'due': None, 'warning': False, 'last_sent': at}
               for key, at in sent.items() if key not in pending]
    if history:
        connection.execute(groups.insert(), history)
    if connection.dialect.name == 'postgresql':
        _postgres()
    else:
        _sqlite()


def _postgres() -> None:
    op.execute('ALTER TABLE error_email_groups ENABLE ROW LEVEL SECURITY')
    op.execute('REVOKE ALL ON error_email_groups FROM PUBLIC, anteroom_seller')
    op.execute('REVOKE ALL ON SEQUENCE error_email_groups_id_seq FROM PUBLIC, anteroom_seller')
    for role in ('anon', 'authenticated'):
        if op.get_bind().scalar(sa.text('SELECT 1 FROM pg_roles WHERE rolname=:role'), {'role': role}):
            op.execute(f'REVOKE ALL ON error_email_groups FROM {role}')
            op.execute(f'REVOKE ALL ON SEQUENCE error_email_groups_id_seq FROM {role}')
    if op.get_bind().scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='anteroom_api'")):
        op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON error_email_groups TO anteroom_api')
        op.execute('GRANT USAGE, SELECT ON SEQUENCE error_email_groups_id_seq TO anteroom_api')
        op.execute('CREATE POLICY operator_email ON error_email_groups TO anteroom_api USING (true) WITH CHECK (true)')
    # Invoker privileges only; neither sellers nor public receive EXECUTE.
    op.execute('''CREATE FUNCTION sync_legacy_error_email_groups() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public AS $$
        BEGIN
          DELETE FROM public.error_email_groups WHERE fingerprint NOT IN (
            SELECT jsonb_object_keys(COALESCE(NEW.payload::jsonb->'groups', '{}'::jsonb))
            UNION SELECT jsonb_object_keys(COALESCE(NEW.payload::jsonb->'last_sent', '{}'::jsonb)))
            OR (payload IS NULL AND fingerprint IN (
              SELECT jsonb_object_keys(COALESCE(NEW.payload::jsonb->'groups', '{}'::jsonb))));
          INSERT INTO public.error_email_groups (fingerprint, payload, count, due, warning, last_sent)
          SELECT keys.key, g.value::json, COALESCE((g.value->>'count')::bigint, 0), (g.value->>'due')::double precision,
            COALESCE(g.value->'report'->>'severity' = 'WARNING', false), (s.value::text)::double precision
          FROM (SELECT jsonb_object_keys(COALESCE(NEW.payload::jsonb->'groups', '{}'::jsonb)) AS key
            UNION SELECT jsonb_object_keys(COALESCE(NEW.payload::jsonb->'last_sent', '{}'::jsonb))) keys
          LEFT JOIN jsonb_each(COALESCE(NEW.payload::jsonb->'groups', '{}'::jsonb)) g ON g.key = keys.key
          LEFT JOIN jsonb_each(COALESCE(NEW.payload::jsonb->'last_sent', '{}'::jsonb)) s ON s.key = keys.key
          ON CONFLICT (fingerprint) DO UPDATE SET payload=excluded.payload, count=excluded.count, due=excluded.due,
            warning=excluded.warning, last_sent=excluded.last_sent
          WHERE error_email_groups.payload::jsonb IS DISTINCT FROM excluded.payload::jsonb
            OR error_email_groups.last_sent IS DISTINCT FROM excluded.last_sent;
          RETURN NEW;
        END $$''')
    op.execute('REVOKE ALL ON FUNCTION sync_legacy_error_email_groups() FROM PUBLIC, anteroom_seller')
    for role in ('anon', 'authenticated'):
        if op.get_bind().scalar(sa.text('SELECT 1 FROM pg_roles WHERE rolname=:role'), {'role': role}):
            op.execute(f'REVOKE ALL ON FUNCTION sync_legacy_error_email_groups() FROM {role}')
    op.execute('''CREATE TRIGGER legacy_error_email_groups AFTER UPDATE OF payload ON error_email_state
        FOR EACH ROW WHEN (NEW.group_sync_version = OLD.group_sync_version AND
          (NEW.payload::jsonb->'groups' IS DISTINCT FROM OLD.payload::jsonb->'groups'
           OR NEW.payload::jsonb->'last_sent' IS DISTINCT FROM OLD.payload::jsonb->'last_sent'))
        EXECUTE FUNCTION sync_legacy_error_email_groups()''')


def _sqlite() -> None:
    op.execute('''CREATE TRIGGER legacy_error_email_groups AFTER UPDATE OF payload ON error_email_state
        WHEN NEW.group_sync_version = OLD.group_sync_version AND
          (json_extract(NEW.payload, '$.groups') IS NOT json_extract(OLD.payload, '$.groups')
           OR json_extract(NEW.payload, '$.last_sent') IS NOT json_extract(OLD.payload, '$.last_sent'))
        BEGIN
          DELETE FROM error_email_groups WHERE fingerprint NOT IN (
            SELECT key FROM json_each(NEW.payload, '$.groups')
            UNION SELECT key FROM json_each(NEW.payload, '$.last_sent'))
            OR (payload IS NULL AND fingerprint IN (SELECT key FROM json_each(NEW.payload, '$.groups')));
          INSERT INTO error_email_groups (fingerprint, payload, count, due, warning, last_sent)
          SELECT keys.key, g.value, COALESCE(json_extract(g.value, '$.count'), 0), json_extract(g.value, '$.due'),
            COALESCE(json_extract(g.value, '$.report.severity') = 'WARNING', 0), s.value
          FROM (SELECT key FROM json_each(NEW.payload, '$.groups')
            UNION SELECT key FROM json_each(NEW.payload, '$.last_sent')) keys
          LEFT JOIN json_each(NEW.payload, '$.groups') g ON g.key = keys.key
          LEFT JOIN json_each(NEW.payload, '$.last_sent') s ON s.key = keys.key
          WHERE true ON CONFLICT (fingerprint) DO UPDATE SET payload=excluded.payload, count=excluded.count, due=excluded.due,
            warning=excluded.warning, last_sent=excluded.last_sent;
        END''')


def downgrade() -> None:
    op.execute('DROP TRIGGER legacy_error_email_groups' + (' ON error_email_state' if op.get_bind().dialect.name == 'postgresql' else ''))
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP FUNCTION sync_legacy_error_email_groups()')
    op.drop_table('error_email_groups')
    with op.batch_alter_table('error_email_state') as batch_op:
        batch_op.drop_column('group_sync_version')
