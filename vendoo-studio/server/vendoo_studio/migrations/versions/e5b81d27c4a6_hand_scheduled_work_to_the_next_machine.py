"""Hand scheduled work and devices to the next Machine.

Revision ID: e5b81d27c4a6
Revises: cd33c540e489
Create Date: 2026-11-07 21:10:00.000000

A blue-green deploy runs the new Machine beside the old one for a few minutes.
The hosted schedulers treat work left in progress at startup as cut off, so
only the Machine holding the ``schedulers`` lease runs them. A Machine that
is told to stop releases the lease and forgets its device routes, so the next
one takes over at once instead of waiting for them to expire.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'e5b81d27c4a6'
down_revision = 'cd33c540e489'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return
    op.execute("""
        CREATE TABLE anteroom_private.process_leases (
            name text PRIMARY KEY,
            machine_id text NOT NULL,
            expires_at timestamptz NOT NULL
        );
        REVOKE ALL ON anteroom_private.process_leases FROM PUBLIC;

        CREATE FUNCTION anteroom_private.hold_process_lease(p_name text, p_machine text, p_seconds integer)
        RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
        DECLARE
            held integer;
        BEGIN
            IF p_name !~ '^[a-z]{1,32}$' OR p_machine !~ '^[A-Za-z0-9]{1,64}$'
                OR p_seconds < 1 OR p_seconds > 300 THEN
                RAISE invalid_parameter_value USING MESSAGE = 'Invalid process lease';
            END IF;
            INSERT INTO anteroom_private.process_leases AS lease (name, machine_id, expires_at)
            VALUES (p_name, p_machine, now() + make_interval(secs => p_seconds))
            ON CONFLICT (name) DO UPDATE
                SET machine_id = EXCLUDED.machine_id, expires_at = EXCLUDED.expires_at
                WHERE lease.machine_id = EXCLUDED.machine_id OR lease.expires_at < now();
            GET DIAGNOSTICS held = ROW_COUNT;
            RETURN held > 0;
        END $$;
        REVOKE ALL ON FUNCTION anteroom_private.hold_process_lease(text, text, integer)
            FROM PUBLIC, anteroom_seller;

        CREATE FUNCTION anteroom_private.release_machine(p_machine text)
        RETURNS void
        LANGUAGE sql SECURITY DEFINER SET search_path='' AS $$
            DELETE FROM anteroom_private.process_leases WHERE machine_id = p_machine;
            DELETE FROM anteroom_private.device_routes WHERE machine_id = p_machine;
        $$;
        REVOKE ALL ON FUNCTION anteroom_private.release_machine(text) FROM PUBLIC, anteroom_seller;
    """)
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='anteroom_api'")):
        op.execute('GRANT EXECUTE ON FUNCTION anteroom_private.hold_process_lease(text, text, integer) TO anteroom_api')
        op.execute('GRANT EXECUTE ON FUNCTION anteroom_private.release_machine(text) TO anteroom_api')


def downgrade() -> None:
    if op.get_bind().dialect.name != 'postgresql':
        return
    op.execute("""
        DROP FUNCTION anteroom_private.release_machine(text);
        DROP FUNCTION anteroom_private.hold_process_lease(text, text, integer);
        DROP TABLE anteroom_private.process_leases;
    """)
