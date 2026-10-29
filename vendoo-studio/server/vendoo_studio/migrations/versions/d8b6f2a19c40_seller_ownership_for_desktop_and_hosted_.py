"""Assign seller ownership without rebuilding or dropping existing data tables.

Revision ID: d8b6f2a19c40
Revises: 3ec056c582ed
Create Date: 2026-10-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "d8b6f2a19c40"
down_revision = "3ec056c582ed"
branch_labels = None
depends_on = None

# Freeze the table list and local owner here: migrations must not change when
# later app models do. Only the bundled public category trees are shared.
LOCAL_USER_ID = "00000000-0000-0000-0000-000000000000"
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


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in SELLER_TABLES:
        # Legacy adoption creates any missing tables from current metadata
        # first, so those tables already have this column and index.
        if "user_id" not in {column["name"] for column in inspector.get_columns(table)}:
            op.add_column(
                table,
                sa.Column("user_id", sa.String(36), nullable=False, server_default=LOCAL_USER_ID),
            )
        index = f"ix_{table}_user_id"
        if index not in {item["name"] for item in inspector.get_indexes(table)}:
            op.create_index(index, table, ["user_id"])


def downgrade() -> None:
    for table in reversed(SELLER_TABLES):
        op.drop_index(f"ix_{table}_user_id", table_name=table)
        op.drop_column(table, "user_id")
