"""Parse each seller's sourcing state once in the timer's due check

Revision ID: b3f8d1c6a2e7
Revises: c728ae43d901
Create Date: 2026-11-20 14:00:00.000000
"""
from __future__ import annotations

from alembic import op


revision = 'b3f8d1c6a2e7'
down_revision = 'c728ae43d901'
branch_labels = None
depends_on = None

# The rule is unchanged from e4b7c2d9f1a3. Only how often the stored value is parsed changes.
CHECK = r"""
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

# Postgres inlines a plain subquery, so `value::jsonb` ran once for every
# reference to it: dozens of parses of a multi-megabyte value (resale data,
# zone charts) every 15 minutes, 11-35 s of database CPU per call. The
# materialized CTEs parse it once and keep only the two small parts compared.
PARSE_ONCE = """
    WITH parsed AS MATERIALIZED (
        SELECT preference.user_id::text AS user_id, preference.value::jsonb AS value
        FROM public.user_preferences AS preference WHERE preference.key = 'sourcing'
    ), state AS MATERIALIZED (
        SELECT parsed.user_id, parsed.value -> 'prefs' AS prefs, parsed.value -> 'snapshot' AS snapshot
        FROM parsed
    )
"""

PARSE_PER_REFERENCE = """
    WITH state AS (
        SELECT preference.user_id::text AS user_id,
               preference.value::jsonb -> 'prefs' AS prefs, preference.value::jsonb -> 'snapshot' AS snapshot
        FROM public.user_preferences AS preference WHERE preference.key = 'sourcing'
    )
"""


def _define(body: str) -> None:
    if op.get_bind().dialect.name != 'postgresql':
        return
    # CREATE OR REPLACE keeps the function's owner, grants and revokes.
    op.execute(f"""
        CREATE OR REPLACE FUNCTION anteroom_private.due_sourcing_owners(
            snapshot_format integer, plan_keys text[], refreshed_before text, now text)
        RETURNS TABLE(owner_id text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path='' AS $$
            {body}{CHECK}
        $$
    """)


def upgrade() -> None:
    _define(PARSE_ONCE)


def downgrade() -> None:
    _define(PARSE_PER_REFERENCE)
