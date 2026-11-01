"""Join the eBay offer owners and seller change feed revisions

Both were written on top of 3b9d6f2a1c47 and merged the same day. Neither
depends on the other; this revision only gives the history one head again.

Revision ID: 5e2b9c7d4a10
Revises: 7c4e1a9f2b38, 8d41c6e2f0a9
Create Date: 2026-11-01 13:00:00.000000
"""
from __future__ import annotations


revision = '5e2b9c7d4a10'
down_revision = ('7c4e1a9f2b38', '8d41c6e2f0a9')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
