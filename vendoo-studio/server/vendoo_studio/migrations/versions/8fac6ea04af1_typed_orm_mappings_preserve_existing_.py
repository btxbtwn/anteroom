"""typed ORM mappings preserve existing schema

Revision ID: 8fac6ea04af1
Revises: 56b5ee7250ec
Create Date: 2026-10-31 06:19:59.341295
"""
from __future__ import annotations



revision = '8fac6ea04af1'
down_revision = '56b5ee7250ec'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Only Python mappings changed; table/index DDL is identical on both databases.
    pass


def downgrade() -> None:
    pass
