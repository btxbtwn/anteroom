"""remove mercari cloud sessions

Mercari is no longer a cloud marketplace (#2029): its edge answers every server
request after the session check with a Cloudflare challenge, so Mercari runs only
in the seller's Chrome. Saved Mercari cloud sign-ins can never be used again, so
they are deleted with their Vault secrets, secret references and operation
journals. The audit history is kept.

Revision ID: e4f6c0a40d63
Revises: e3b71c5a90d4
Create Date: 2026-11-24 06:23:41.512000
"""
from __future__ import annotations

from alembic import op


revision = 'e4f6c0a40d63'
down_revision = 'e3b71c5a90d4'
branch_labels = None
depends_on = None

SLOT = 'cloud-session-mercari'


def upgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        # By id and full name, as secret_write checks: a corrupt reference never reaches another seller's secret.
        op.execute(f"""
            DO $$ BEGIN
                IF to_regclass('vault.secrets') IS NOT NULL THEN
                    DELETE FROM vault.secrets AS secret
                    USING public.user_secret_references AS reference
                    WHERE reference.account = '{SLOT}'
                      AND secret.id = reference.secret_id
                      AND secret.name = 'anteroom:' || reference.user_id || ':' || '{SLOT}';
                END IF;
            END $$
        """)
    op.execute(f"DELETE FROM user_secret_references WHERE account = '{SLOT}'")
    op.execute("DELETE FROM cloud_operation_journals WHERE marketplace = 'mercari'")
    op.execute("DELETE FROM cloud_marketplace_sessions WHERE marketplace = 'mercari'")


def downgrade() -> None:
    # Deleted sign-ins cannot be restored; the seller signs in to Mercari in Chrome.
    pass
