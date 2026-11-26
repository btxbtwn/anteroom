"""Add opt-in hosted listing quality observations."""

import sqlalchemy as sa
from alembic import op

revision = "1d5c92e7a41b"
down_revision = "d5e1b7a3c962"
branch_labels = None
depends_on = None

TABLE = "listing_quality_attempts"
LOCAL_OWNER = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(TABLE):
        op.create_table(
            TABLE,
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(36), nullable=False, server_default=LOCAL_OWNER, index=True),
            sa.Column("conversation_id", sa.String(), nullable=False, index=True),
            sa.Column("revision_id", sa.String(), nullable=False, unique=True),
            sa.Column("generated_revision_id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("corrections", sa.JSON(), nullable=False),
            sa.Column("specific_slots", sa.JSON(), nullable=False),
            sa.Column("overwrite_compared", sa.Boolean(), nullable=False),
            sa.Column("overwrites", sa.JSON(), nullable=False),
            sa.ForeignKeyConstraint(
                ["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
                name="fk_listing_quality_attempts_seller_conversation_id", ondelete="CASCADE",
            ),
        )
    if bind.dialect.name != "postgresql":
        return
    owner = (
        "user_id = (SELECT nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub') "
        f"AND user_id <> '{LOCAL_OWNER}'"
    )
    op.execute(f"ALTER TABLE public.{TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"REVOKE ALL ON public.{TABLE} FROM PUBLIC")
    for role in ("anon", "authenticated"):
        if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}):
            op.execute(f"REVOKE ALL ON public.{TABLE} FROM {role}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON public.{TABLE} TO anteroom_seller")
    op.execute(
        f"CREATE POLICY seller_owner ON public.{TABLE} AS RESTRICTIVE FOR ALL TO PUBLIC "
        f"USING ({owner}) WITH CHECK ({owner})"
    )
    op.execute(
        f"CREATE POLICY seller_access ON public.{TABLE} FOR ALL TO anteroom_seller "
        "USING (true) WITH CHECK (true)"
    )
    op.execute(
        f"CREATE TRIGGER account_write_guard BEFORE INSERT OR UPDATE ON public.{TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION anteroom_private.guard_account_write()"
    )
    op.execute("""
        CREATE FUNCTION anteroom_private.listing_quality_report(p_since timestamptz, p_until timestamptz)
        RETURNS TABLE (
            generated_attempts bigint, reviewed_attempts bigint, corrected_attempts bigint,
            overwrite_compared_attempts bigint, overwritten_attempts bigint, corrections json,
            overwrites json, field_rankings json
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
            WITH attempts AS MATERIALIZED (
                SELECT attempt.user_id, attempt.reviewed_at, attempt.corrections, attempt.overwrite_compared,
                       attempt.overwrites
                  FROM public.listing_quality_attempts AS attempt
                 WHERE attempt.created_at >= p_since AND attempt.created_at < p_until
            ), correction_counts AS (
                SELECT field.key AS field, change.key AS change, sum(change.value::integer) AS count
                  FROM attempts
                  CROSS JOIN LATERAL pg_catalog.json_each(attempts.corrections) AS field
                  CROSS JOIN LATERAL pg_catalog.json_each_text(field.value) AS change
                 GROUP BY field.key, change.key
            ), correction_fields AS (
                SELECT field, pg_catalog.json_object_agg(change, count) AS counts
                  FROM correction_counts GROUP BY field
            ), ranked_fields AS (
                SELECT field.key AS field, count(*) AS corrected_attempts,
                       count(DISTINCT attempts.user_id) AS affected_sellers
                  FROM attempts
                  CROSS JOIN LATERAL pg_catalog.json_each(attempts.corrections) AS field
                 WHERE attempts.reviewed_at IS NOT NULL
                 GROUP BY field.key
            ), review_totals AS (
                SELECT count(*) FILTER (WHERE reviewed_at IS NOT NULL) AS reviewed_attempts
                  FROM attempts
            ), overwrite_counts AS (
                SELECT stage.key AS stage, field.key AS field, change.key AS change,
                       sum(change.value::integer) AS count
                  FROM attempts
                  CROSS JOIN LATERAL pg_catalog.json_each(attempts.overwrites) AS stage
                  CROSS JOIN LATERAL pg_catalog.json_each(stage.value) AS field
                  CROSS JOIN LATERAL pg_catalog.json_each_text(field.value) AS change
                 GROUP BY stage.key, field.key, change.key
            ), overwrite_fields AS (
                SELECT stage, field, pg_catalog.json_object_agg(change, count) AS counts
                  FROM overwrite_counts GROUP BY stage, field
            ), overwrite_stages AS (
                SELECT stage, pg_catalog.json_object_agg(field, counts) AS fields
                  FROM overwrite_fields GROUP BY stage
            )
            SELECT count(*), count(*) FILTER (WHERE reviewed_at IS NOT NULL),
                   count(*) FILTER (WHERE attempts.corrections::jsonb <> '{}'::jsonb),
                   count(*) FILTER (WHERE attempts.overwrite_compared),
                   count(*) FILTER (WHERE attempts.overwrites::jsonb <> '{}'::jsonb),
                   coalesce((SELECT pg_catalog.json_object_agg(field, counts) FROM correction_fields), '{}'::json),
                   coalesce((SELECT pg_catalog.json_object_agg(stage, fields) FROM overwrite_stages), '{}'::json),
                   coalesce((
                       SELECT pg_catalog.json_agg(pg_catalog.json_build_object(
                           'field', ranked_fields.field,
                           'corrected_attempts', ranked_fields.corrected_attempts,
                           'affected_sellers', ranked_fields.affected_sellers,
                           'edit_rate', ranked_fields.corrected_attempts::double precision / review_totals.reviewed_attempts
                       ) ORDER BY ranked_fields.corrected_attempts DESC, ranked_fields.field)
                         FROM ranked_fields CROSS JOIN review_totals
                   ), '[]'::json)
              FROM attempts
        $$;
    """)
    function = "anteroom_private.listing_quality_report(timestamptz, timestamptz)"
    op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC, anteroom_seller")
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='anteroom_api'")):
        op.execute("GRANT USAGE ON SCHEMA anteroom_private TO anteroom_api")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO anteroom_api")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS anteroom_private.listing_quality_report(timestamptz, timestamptz)")
    op.drop_table(TABLE)
