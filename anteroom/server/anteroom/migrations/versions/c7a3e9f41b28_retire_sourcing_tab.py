"""retire sourcing tab and scout checks

The Sourcing buy-list, scraper, and Scout an item tool are retired (#2073),
while bought boxes (source_boxes) remain under Analytics. Drop the retired
scout_checks table, the PostgreSQL sourcing scheduler helper function, and
stored sourcing preference/change-feed rows.

Revision ID: c7a3e9f41b28
Revises: e4b8c2a91d07
Create Date: 2026-11-24 14:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'c7a3e9f41b28'
down_revision = 'e4b8c2a91d07'
branch_labels = None
depends_on = None


LOCAL_USER_ID = '00000000-0000-0000-0000-000000000000'
SIGNATURE = 'anteroom_private.due_sourcing_owners(integer, text[], text, text)'
OWNER_CHECK = """(
    user_id = (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
    AND user_id <> '00000000-0000-0000-0000-000000000000'
)"""


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table('scout_checks'):
        op.drop_table('scout_checks')
    op.execute("DELETE FROM user_preferences WHERE key = 'sourcing'")
    op.execute("DELETE FROM seller_changes WHERE topic = 'sourcing'")
    if bind.dialect.name == 'postgresql':
        op.execute(f'DROP FUNCTION IF EXISTS {SIGNATURE}')


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table('scout_checks'):
        op.create_table(
            'scout_checks',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('user_id', sa.String(36), nullable=False, server_default=LOCAL_USER_ID),
            sa.Column('status', sa.String(), nullable=False),
            sa.Column('asking_price', sa.Float(), nullable=True),
            sa.Column('photos', sa.JSON(), nullable=False),
            sa.Column('title', sa.String(), nullable=True),
            sa.Column('analysis', sa.Text(), nullable=True),
            sa.Column('comps', sa.Text(), nullable=True),
            sa.Column('estimate', sa.Float(), nullable=True),
            sa.Column('comps_count', sa.Integer(), nullable=True),
            sa.Column('error', sa.Text(), nullable=True),
            sa.Column('decision', sa.String(), nullable=True),
            sa.Column('conversation_id', sa.String(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('decided_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(
                ['user_id', 'conversation_id'],
                ['conversations.user_id', 'conversations.id'],
                name='fk_scout_checks_seller_conversation_id',
                deferrable=True,
                initially='DEFERRED',
            ),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_scout_checks_conversation_id', 'scout_checks', ['conversation_id'], unique=False)
        op.create_index('ix_scout_checks_user_id', 'scout_checks', ['user_id'], unique=False)
    if bind.dialect.name == 'postgresql':
        op.execute('ALTER TABLE "scout_checks" ENABLE ROW LEVEL SECURITY')
        op.execute(
            'CREATE POLICY seller_owner ON "scout_checks" AS RESTRICTIVE FOR ALL TO PUBLIC '
            f'USING ({OWNER_CHECK}) WITH CHECK ({OWNER_CHECK})'
        )
        op.execute(
            'CREATE POLICY seller_access ON "scout_checks" FOR ALL TO anteroom_seller '
            'USING (true) WITH CHECK (true)'
        )
        op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "scout_checks" TO anteroom_seller')
        op.execute(r"""
            CREATE OR REPLACE FUNCTION anteroom_private.due_sourcing_owners(
                snapshot_format integer, plan_keys text[], refreshed_before text, now text)
            RETURNS TABLE(owner_id text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path='' AS $$
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
            $$;
            REVOKE ALL ON FUNCTION anteroom_private.due_sourcing_owners(integer, text[], text, text)
                FROM PUBLIC, anteroom_seller;
        """)
        if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='anteroom_api'")):
            op.execute(f'GRANT EXECUTE ON FUNCTION {SIGNATURE} TO anteroom_api')
