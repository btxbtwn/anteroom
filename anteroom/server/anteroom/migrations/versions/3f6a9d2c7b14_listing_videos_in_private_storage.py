"""Listing videos in the private photo bucket

Hosted listing videos live in the seller's own folder of the private
``anteroom-photos`` bucket, so its owner-only policies, the account-deletion
fence and account deletion cover them unchanged. The bucket now also takes the
video formats eBay, Etsy and Poshmark accept, up to eBay's 150 MB. The API
still bounds photos at their own size and type.

Revision ID: 3f6a9d2c7b14
Revises: 049b76a1ce20
Create Date: 2026-11-22 12:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '3f6a9d2c7b14'
down_revision = '049b76a1ce20'
branch_labels = None
depends_on = None

TYPES = ("ARRAY['image/jpeg', 'video/mp4', 'video/quicktime', 'video/x-flv', 'video/x-msvideo', "
         "'video/3gpp', 'video/mpeg']")


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != 'postgresql' or connection.scalar(sa.text("SELECT to_regclass('storage.buckets')")) is None:
        # Mac SQLite and ordinary Postgres have no Supabase-managed Storage schema.
        return
    op.execute(f"""UPDATE storage.buckets SET public = false, file_size_limit = 157286400,
        allowed_mime_types = {TYPES} WHERE id = 'anteroom-photos'""")


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != 'postgresql' or connection.scalar(sa.text("SELECT to_regclass('storage.buckets')")) is None:
        return
    op.execute("""UPDATE storage.buckets SET public = false, file_size_limit = 10485760,
        allowed_mime_types = ARRAY['image/jpeg'] WHERE id = 'anteroom-photos'""")
