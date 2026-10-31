"""typed ORM mappings preserve existing schema

Revision ID: 8fac6ea04af1
Revises: b4e9d2c7a813
Create Date: 2026-10-31 06:19:59.341295
"""
from __future__ import annotations

revision = '8fac6ea04af1'
down_revision = 'b4e9d2c7a813'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Only Python mappings changed; table/index DDL is identical on both databases.
    pass


def downgrade() -> None:
    pass
