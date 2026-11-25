"""Release a stopping Machine's device routes before its scheduler lease.

Revision ID: f3c8a1d6e270
Revises: c4ae31f62b90
Create Date: 2026-11-25 15:00:00.000000

A stopping Machine closes its listening socket at once but may keep the
``schedulers`` lease for a short grace while a relist or scheduled publish
finishes. Its Mac and Chrome routes have to move to the next Machine right
away, or the seller's devices are replayed to a socket that no longer accepts
connections. ``release_machine_routes`` frees only the routes.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'f3c8a1d6e270'
down_revision = 'c4ae31f62b90'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return
    op.execute("""
        CREATE FUNCTION anteroom_private.release_machine_routes(p_machine text)
        RETURNS void
        LANGUAGE sql SECURITY DEFINER SET search_path='' AS $$
            DELETE FROM anteroom_private.device_routes WHERE machine_id = p_machine;
        $$;
        REVOKE ALL ON FUNCTION anteroom_private.release_machine_routes(text) FROM PUBLIC, anteroom_seller;
    """)
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='anteroom_api'")):
        op.execute('GRANT EXECUTE ON FUNCTION anteroom_private.release_machine_routes(text) TO anteroom_api')


def downgrade() -> None:
    if op.get_bind().dialect.name != 'postgresql':
        return
    op.execute('DROP FUNCTION anteroom_private.release_machine_routes(text)')
