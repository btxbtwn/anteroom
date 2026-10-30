"""Private Supabase listing photo storage

Revision ID: c25ff7e26196
Revises: 84bc2a03cbad
Create Date: 2026-10-30 02:39:29.060996
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'c25ff7e26196'
down_revision = '84bc2a03cbad'
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != 'postgresql' or connection.scalar(sa.text("SELECT to_regclass('storage.objects')")) is None:
        # Mac SQLite and ordinary Postgres have no Supabase-managed Storage schema.
        return
    op.execute("""
        INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
        VALUES ('anteroom-photos', 'anteroom-photos', false, 10485760, ARRAY['image/jpeg'])
        ON CONFLICT (id) DO UPDATE SET public = false, file_size_limit = 10485760,
            allowed_mime_types = ARRAY['image/jpeg']
    """)
    owner = """bucket_id = 'anteroom-photos'
        AND (storage.foldername(name))[1] = (SELECT auth.uid())::text
        AND coalesce((auth.jwt() ->> 'is_anonymous')::boolean, true) = false"""
    # Restrictive guard also prevents a future broad Storage policy from opening
    # this bucket. A permissive policy supplies access only to authenticated owners.
    op.execute(f"""CREATE POLICY anteroom_photo_guard ON storage.objects AS RESTRICTIVE
        FOR ALL TO PUBLIC USING (bucket_id <> 'anteroom-photos' OR ({owner}))
        WITH CHECK (bucket_id <> 'anteroom-photos' OR ({owner}))""")
    for action in ('SELECT', 'INSERT', 'DELETE'):
        clause = f'WITH CHECK ({owner})' if action == 'INSERT' else f'USING ({owner})'
        op.execute(f'CREATE POLICY anteroom_photo_{action.lower()} ON storage.objects '
                   f'FOR {action} TO authenticated {clause}')


def downgrade() -> None:
    raise RuntimeError("Photo storage policies are forward-only; never remove protection from stored photos")
