"""Let the trusted web process find sellers whose Sourcing refresh is due

Revision ID: e4b7c2d9f1a3
Revises: a6f3d1c8e925
Create Date: 2026-11-02 12:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'e4b7c2d9f1a3'
down_revision = 'a6f3d1c8e925'
branch_labels = None
depends_on = None

SIGNATURE = 'anteroom_private.due_sourcing_owners(integer, text[], text, text)'


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return
    # Returns owner IDs only. The timer then enters each seller's own
    # row-secured session and checks box_scout.refresh_is_due there before it
    # refreshes; HTTP handlers (anteroom_seller) cannot call this. The format,
    # plan keys and time bounds come from the caller, so this stays in step with
    # box_scout without a new revision. Snapshot times are compared as the UTC
    # ISO text box_scout writes, so a malformed value lists the seller instead
    # of failing a cast for everyone.
    op.execute(r"""
        CREATE FUNCTION anteroom_private.due_sourcing_owners(
            snapshot_format integer, plan_keys text[], refreshed_before text, now text)
        RETURNS TABLE(owner_id text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path='' AS $$
            SELECT state.user_id FROM (
                SELECT preference.user_id::text AS user_id, preference.value::jsonb AS value,
                       preference.value::jsonb -> 'snapshot' AS snapshot
                FROM public.user_preferences AS preference WHERE preference.key = 'sourcing'
            ) AS state
            WHERE jsonb_typeof(state.snapshot) IS DISTINCT FROM 'object'
                OR state.snapshot -> 'format' IS DISTINCT FROM to_jsonb(snapshot_format)
                OR jsonb_typeof(state.snapshot -> 'preferences') IS DISTINCT FROM 'object'
                OR NOT (state.snapshot -> 'preferences' ?& plan_keys)
                OR jsonb_typeof(state.snapshot -> 'updated_at') IS DISTINCT FROM 'string'
                OR state.snapshot ->> 'updated_at' !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}'
                OR state.snapshot ->> 'updated_at' <= refreshed_before
                OR state.snapshot ->> 'updated_at' > now
                OR (jsonb_typeof(state.value -> 'prefs') = 'object' AND EXISTS (
                    SELECT 1 FROM unnest(plan_keys) AS plan_key
                    WHERE state.value -> 'prefs' ? plan_key
                      AND state.value -> 'prefs' -> plan_key
                          IS DISTINCT FROM state.snapshot -> 'preferences' -> plan_key))
        $$;
        REVOKE ALL ON FUNCTION anteroom_private.due_sourcing_owners(integer, text[], text, text)
            FROM PUBLIC, anteroom_seller;
    """)
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='anteroom_api'")):
        op.execute(f'GRANT EXECUTE ON FUNCTION {SIGNATURE} TO anteroom_api')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.execute(f'DROP FUNCTION {SIGNATURE}')
