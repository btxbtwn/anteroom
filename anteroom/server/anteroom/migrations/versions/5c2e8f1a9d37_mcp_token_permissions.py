"""Per-token MCP permissions

Each MCP access token records what the seller lets it reach, per area. Tokens
made before this revision keep the access every token had until now: read and
write listings, read analytics and Needs Attention. The column default only
fills those existing rows (no row triggers fire); new tokens always set their
own permissions.

Revision ID: 5c2e8f1a9d37
Revises: 8e4b2c9a1d7f
Create Date: 2026-11-22 22:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '5c2e8f1a9d37'
down_revision = '8e4b2c9a1d7f'
branch_labels = None
depends_on = None

EXISTING_TOKEN_ACCESS = '{"listings": "write", "analytics": "read", "attention": "read"}'


def upgrade() -> None:
    # A database adopted from before migrations is built from the current models
    # and already has the column.
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns('mcp_access_tokens')}
    if 'permissions' in columns:
        return
    with op.batch_alter_table('mcp_access_tokens', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'permissions', sa.JSON(), nullable=False, server_default=EXISTING_TOKEN_ACCESS,
        ))
    with op.batch_alter_table('mcp_access_tokens', schema=None) as batch_op:
        batch_op.alter_column('permissions', existing_type=sa.JSON(), server_default=None)


def downgrade() -> None:
    with op.batch_alter_table('mcp_access_tokens', schema=None) as batch_op:
        batch_op.drop_column('permissions')
