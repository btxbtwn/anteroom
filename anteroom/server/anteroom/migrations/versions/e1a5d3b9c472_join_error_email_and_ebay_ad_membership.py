"""Join the error email and eBay ad membership revisions.

Both revisions descend from 4cf6b35245d2 and change independent tables.

Revision ID: e1a5d3b9c472
Revises: 85dab8d3327c, b0d24fa61ce8
Create Date: 2026-11-21 10:45:19.621222
"""
from __future__ import annotations

revision = 'e1a5d3b9c472'
down_revision = ('85dab8d3327c', 'b0d24fa61ce8')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
