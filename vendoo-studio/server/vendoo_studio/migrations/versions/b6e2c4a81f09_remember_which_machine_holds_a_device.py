"""Remember which machine a seller's Mac or Chrome is polling.

Revision ID: b6e2c4a81f09
Revises: 9a46c0f2d381
Create Date: 2026-11-02 21:40:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'b6e2c4a81f09'
down_revision = '9a46c0f2d381'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return
    op.execute("""
        CREATE TABLE anteroom_private.device_routes (
            user_id uuid NOT NULL,
            device text NOT NULL,
            machine_id text NOT NULL,
            base_url text NOT NULL,
            seen_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (user_id, device),
            CONSTRAINT device_routes_device CHECK (device IN ('mac', 'chrome'))
        );
        REVOKE ALL ON anteroom_private.device_routes FROM PUBLIC;

        CREATE FUNCTION anteroom_private.note_device_route(
            p_owner uuid, p_device text, p_machine text, p_url text, p_stale integer
        ) RETURNS TABLE(machine_id text, base_url text, owned boolean)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
        BEGIN
            IF p_device NOT IN ('mac', 'chrome')
                OR p_machine !~ '^[A-Za-z0-9]{1,64}$'
                OR p_url !~ '^http://(\\[[0-9a-f:]+\\]|127\\.0\\.0\\.1):[0-9]{2,5}$'
                OR p_stale < 1 OR p_stale > 120 THEN
                RAISE invalid_parameter_value USING MESSAGE = 'Invalid device route';
            END IF;
            INSERT INTO anteroom_private.device_routes AS route (user_id, device, machine_id, base_url, seen_at)
            VALUES (p_owner, p_device, p_machine, p_url, now())
            ON CONFLICT (user_id, device) DO UPDATE
                SET machine_id = EXCLUDED.machine_id, base_url = EXCLUDED.base_url, seen_at = now()
                WHERE route.machine_id = EXCLUDED.machine_id
                   OR route.seen_at < now() - make_interval(secs => p_stale);
            RETURN QUERY
                SELECT route.machine_id, route.base_url, route.machine_id = p_machine
                FROM anteroom_private.device_routes AS route
                WHERE route.user_id = p_owner AND route.device = p_device;
        END $$;
        REVOKE ALL ON FUNCTION anteroom_private.note_device_route(uuid, text, text, text, integer)
            FROM PUBLIC, anteroom_seller;

        CREATE FUNCTION anteroom_private.device_route(p_owner uuid, p_device text, p_stale integer)
        RETURNS TABLE(machine_id text, base_url text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path='' AS $$
            SELECT route.machine_id, route.base_url
            FROM anteroom_private.device_routes AS route
            WHERE route.user_id = p_owner AND route.device = p_device
                AND route.seen_at > now() - make_interval(secs => p_stale)
        $$;
        REVOKE ALL ON FUNCTION anteroom_private.device_route(uuid, text, integer) FROM PUBLIC, anteroom_seller;
    """)
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='anteroom_api'")):
        op.execute('GRANT EXECUTE ON FUNCTION anteroom_private.note_device_route(uuid, text, text, text, integer) TO anteroom_api')
        op.execute('GRANT EXECUTE ON FUNCTION anteroom_private.device_route(uuid, text, integer) TO anteroom_api')


def downgrade() -> None:
    raise RuntimeError('Device route migrations are forward-only')
