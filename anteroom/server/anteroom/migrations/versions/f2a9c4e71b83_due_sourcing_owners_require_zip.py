"""Skip sellers without a configured destination ZIP in due_sourcing_owners

Revision ID: f2a9c4e71b83
Revises: e1a5d3b9c472
Create Date: 2026-11-21 13:00:00.000000
"""
from __future__ import annotations

from alembic import op


revision = 'f2a9c4e71b83'
down_revision = 'e1a5d3b9c472'
branch_labels = None
depends_on = None

PARSE_ONCE = r"""
    WITH parsed AS MATERIALIZED (
        SELECT
            preference.user_id::text AS user_id,
            preference.value::jsonb AS value,
            CASE WHEN jsonb_typeof(settings.value::jsonb) = 'object' THEN settings.value::jsonb END AS settings
        FROM public.user_preferences AS preference
        LEFT JOIN public.user_preferences AS settings
            ON settings.user_id = preference.user_id AND settings.key = 'settings'
        WHERE preference.key = 'sourcing'
    ), state AS MATERIALIZED (
        SELECT
            parsed.user_id,
            parsed.value -> 'prefs' AS prefs,
            parsed.value -> 'snapshot' AS snapshot,
            COALESCE(
                NULLIF(CASE WHEN jsonb_typeof(parsed.value -> 'prefs') = 'object'
                            THEN parsed.value -> 'prefs' ->> 'zip' END, ''),
                substring(btrim(COALESCE(parsed.settings ->> 'ships_from_zip', '')) FROM '^(\d{5})(?:-\d{4})?$'),
                substring(btrim(COALESCE(parsed.settings ->> 'ebay_inventory_location', '')) FROM '^(\d{5})(?:-\d{4})?$'),
                ''
            ) AS effective_zip
        FROM parsed
    )
"""

CHECK_WITHOUT_ZIP_GUARD = r"""
    SELECT state.user_id FROM state
    WHERE jsonb_typeof(state.snapshot) IS DISTINCT FROM 'object'
        OR state.snapshot -> 'format' IS DISTINCT FROM to_jsonb(snapshot_format)
        OR jsonb_typeof(state.snapshot -> 'preferences') IS DISTINCT FROM 'object'
        OR NOT (state.snapshot -> 'preferences' ?& plan_keys)
        OR jsonb_typeof(state.snapshot -> 'updated_at') IS DISTINCT FROM 'string'
        OR state.snapshot ->> 'updated_at' !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}'
        OR state.snapshot ->> 'updated_at' <= refreshed_before
        OR state.snapshot ->> 'updated_at' > now
        OR (jsonb_typeof(state.prefs) = 'object' AND EXISTS (
            SELECT 1 FROM unnest(plan_keys) AS plan_key
            WHERE state.prefs ? plan_key
              AND state.prefs -> plan_key IS DISTINCT FROM state.snapshot -> 'preferences' -> plan_key))
"""

CHECK_WITH_ZIP_GUARD = r"""
    SELECT state.user_id FROM state
    WHERE state.effective_zip ~ '^\d{5}$'
        AND state.effective_zip !~ '^(00[6-9]|09\d|340|96[2-9]|99[5-9])'
        AND (
            jsonb_typeof(state.snapshot) IS DISTINCT FROM 'object'
            OR state.snapshot -> 'format' IS DISTINCT FROM to_jsonb(snapshot_format)
            OR jsonb_typeof(state.snapshot -> 'preferences') IS DISTINCT FROM 'object'
            OR NOT (state.snapshot -> 'preferences' ?& plan_keys)
            OR jsonb_typeof(state.snapshot -> 'updated_at') IS DISTINCT FROM 'string'
            OR state.snapshot ->> 'updated_at' !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}'
            OR state.snapshot ->> 'updated_at' <= refreshed_before
            OR state.snapshot ->> 'updated_at' > now
            OR (state.snapshot -> 'preferences' ->> 'zip') IS DISTINCT FROM state.effective_zip
            OR (jsonb_typeof(state.prefs) = 'object' AND EXISTS (
                SELECT 1 FROM unnest(plan_keys) AS plan_key
                WHERE plan_key <> 'zip'
                  AND state.prefs ? plan_key
                  AND state.prefs -> plan_key IS DISTINCT FROM state.snapshot -> 'preferences' -> plan_key))
        )
"""


def _define(check: str) -> None:
    if op.get_bind().dialect.name != 'postgresql':
        return
    op.execute(f"""
        CREATE OR REPLACE FUNCTION anteroom_private.due_sourcing_owners(
            snapshot_format integer, plan_keys text[], refreshed_before text, now text)
        RETURNS TABLE(owner_id text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path='' AS $$
            {PARSE_ONCE}{check}
        $$
    """)


def upgrade() -> None:
    _define(CHECK_WITH_ZIP_GUARD)


def downgrade() -> None:
    _define(CHECK_WITHOUT_ZIP_GUARD)
