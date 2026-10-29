"""Restrict hosted seller reads and writes with PostgreSQL row security.

Revision ID: f1c84a9d7b32
Revises: d8b6f2a19c40
"""
from alembic import op
import sqlalchemy as sa

revision = "f1c84a9d7b32"
down_revision = "d8b6f2a19c40"
branch_labels = None
depends_on = None

SELLER_TABLES = (
    "ad_spend",
    "assistant_memories",
    "assistant_messages",
    "assistant_threads",
    "automation_applications",
    "automation_approvals",
    "automation_relist_gaps",
    "automation_runs",
    "automations",
    "category_field_schemas",
    "category_nodes",
    "category_schemas",
    "conversations",
    "diagnostic_runs",
    "ebay_actions",
    "ebay_offer_attempts",
    "ebay_offer_rules",
    "field_observations",
    "field_registry",
    "fill_log_entries",
    "generation_timings",
    "item_price_baselines",
    "job_events",
    "jobs",
    "listing_copy_receipts",
    "listing_corrections",
    "listing_promotions",
    "listing_revisions",
    "listing_templates",
    "listing_videos",
    "listings",
    "marketplace_listings",
    "marketplace_offer_floors",
    "marketplace_sales",
    "messages",
    "photo_fingerprints",
    "photos",
    "promotion_automations",
    "promotion_runs",
    "sale_events",
    "sale_snapshots",
    "scout_checks",
    "similar_item_reviews",
    "source_boxes",
    "vendoo_draft_cache",
)
OWNER_CHECK = """(
    user_id = (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
    AND user_id <> '00000000-0000-0000-0000-000000000000'
)"""


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # Roles belong to the cluster, so another database's concurrent migration
    # may create this same unprivileged role first.
    op.execute("""DO $$
        BEGIN
            CREATE ROLE anteroom_seller NOLOGIN NOINHERIT NOSUPERUSER
                NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
        EXCEPTION WHEN duplicate_object OR unique_violation THEN NULL;
        END $$""")
    unsafe = bind.execute(sa.text("""
        SELECT rolsuper OR rolbypassrls OR rolcanlogin OR rolinherit OR rolcreatedb
            OR rolcreaterole OR rolreplication
        FROM pg_roles WHERE rolname = 'anteroom_seller'
    """)).scalar_one()
    if unsafe:
        raise RuntimeError("anteroom_seller must be an unprivileged NOLOGIN NOINHERIT role")
    op.execute("GRANT anteroom_seller TO CURRENT_USER")
    op.execute("GRANT USAGE ON SCHEMA public TO anteroom_seller")
    for table in SELLER_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        # Restrictive policies also constrain any future permissive policy.
        op.execute(
            f'CREATE POLICY seller_owner ON "{table}" AS RESTRICTIVE FOR ALL TO PUBLIC '
            f'USING ({OWNER_CHECK}) WITH CHECK ({OWNER_CHECK})'
        )
        op.execute(
            f'CREATE POLICY seller_access ON "{table}" FOR ALL TO anteroom_seller '
            'USING (true) WITH CHECK (true)'
        )
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table}" TO anteroom_seller')
    op.execute("GRANT SELECT ON category_trees, category_tree_nodes TO anteroom_seller")
    for table in ("category_trees", "category_tree_nodes"):
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY shared_catalog_read ON "{table}" FOR SELECT TO PUBLIC USING (true)')


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in reversed(SELLER_TABLES):
        op.execute(f'REVOKE SELECT, INSERT, UPDATE, DELETE ON "{table}" FROM anteroom_seller')
        op.execute(f'DROP POLICY seller_access ON "{table}"')
        op.execute(f'DROP POLICY seller_owner ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
    op.execute("REVOKE SELECT ON category_trees, category_tree_nodes FROM anteroom_seller")
    for table in ("category_trees", "category_tree_nodes"):
        op.execute(f'DROP POLICY shared_catalog_read ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
    # The role and its memberships may serve other databases in this cluster.
