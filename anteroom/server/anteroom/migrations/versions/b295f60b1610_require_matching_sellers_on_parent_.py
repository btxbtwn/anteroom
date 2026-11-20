"""Require matching sellers on existing parent/child references.

Revision ID: b295f60b1610
Revises: a4a9eee52599
"""
from __future__ import annotations

from collections import defaultdict

from alembic import op
import sqlalchemy as sa

revision = "b295f60b1610"
down_revision = "a4a9eee52599"
branch_labels = None
depends_on = None

# Frozen schema: do not import live models into a migration.
PARENTS = ('automations', 'conversations', 'diagnostic_runs', 'ebay_offer_rules', 'jobs', 'promotion_automations', 'source_boxes')
REFERENCES = (
    ('automation_applications', 'automation_id', 'automations'),
    ('automation_applications', 'conversation_id', 'conversations'),
    ('automation_approvals', 'automation_id', 'automations'),
    ('automation_approvals', 'conversation_id', 'conversations'),
    ('automation_relist_gaps', 'automation_id', 'automations'),
    ('automation_relist_gaps', 'conversation_id', 'conversations'),
    ('automation_runs', 'automation_id', 'automations'),
    ('conversations', 'box_id', 'source_boxes'),
    ('diagnostic_runs', 'job_id', 'jobs'),
    ('ebay_offer_attempts', 'rule_id', 'ebay_offer_rules'),
    ('ebay_offer_rules', 'conversation_id', 'conversations'),
    ('field_observations', 'diagnostic_run_id', 'diagnostic_runs'),
    ('fill_log_entries', 'job_id', 'jobs'),
    ('item_price_baselines', 'conversation_id', 'conversations'),
    ('job_events', 'job_id', 'jobs'),
    ('jobs', 'conversation_id', 'conversations'),
    ('listing_copy_receipts', 'conversation_id', 'conversations'),
    ('listing_corrections', 'conversation_id', 'conversations'),
    ('listing_promotions', 'automation_id', 'promotion_automations'),
    ('listing_promotions', 'conversation_id', 'conversations'),
    ('listing_revisions', 'conversation_id', 'conversations'),
    ('listing_videos', 'conversation_id', 'conversations'),
    ('listings', 'conversation_id', 'conversations'),
    ('marketplace_listings', 'conversation_id', 'conversations'),
    ('marketplace_offer_floors', 'conversation_id', 'conversations'),
    ('marketplace_sales', 'conversation_id', 'conversations'),
    ('messages', 'conversation_id', 'conversations'),
    ('photos', 'conversation_id', 'conversations'),
    ('promotion_runs', 'automation_id', 'promotion_automations'),
    ('sale_snapshots', 'conversation_id', 'conversations'),
    ('scout_checks', 'conversation_id', 'conversations'),
    ('similar_item_reviews', 'first_id', 'conversations'),
    ('similar_item_reviews', 'second_id', 'conversations'),
    ('vendoo_draft_cache', 'job_id', 'jobs'),

)


def upgrade() -> None:
    connection = op.get_bind()
    # Check before any table rebuild, including SQLite where the migration
    # connection disables enforcement. Never repair ownership by guessing or
    # deleting rows. A mismatched database needs explicit owner reconciliation.
    for child, column, parent in REFERENCES:
        mismatch = connection.execute(sa.text(f"""
            SELECT 1 FROM "{child}" AS child
            WHERE child."{column}" IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM "{parent}" AS parent
                WHERE parent.id = child."{column}" AND parent.user_id = child.user_id
            ) LIMIT 1
        """)).first()
        if mismatch is not None:
            raise RuntimeError(f"Seller ownership mismatch in {child}.{column}; database upgrade refused")

    # PostgreSQL requires the referenced unique key to exist before the FK.
    for parent in PARENTS:
        name = f"uq_{parent}_seller_id"
        if any(key["name"] == name for key in sa.inspect(connection).get_unique_constraints(parent)):
            continue  # A table created during legacy adoption already has it.
        with op.batch_alter_table(parent) as batch:
            batch.create_unique_constraint(name, ["user_id", "id"])

    grouped = defaultdict(list)
    for child, column, parent in REFERENCES:
        grouped[child].append((column, parent))
    for child, references in grouped.items():
        existing = {key["name"] for key in sa.inspect(connection).get_foreign_keys(child)}
        with op.batch_alter_table(child) as batch:
            for column, parent in references:
                name = f"fk_{child}_seller_{column}"
                if name not in existing:
                    # The original single-column FK retains CASCADE/SET NULL.
                    # This guard checks the final transaction state without
                    # clearing user_id when an optional parent is deleted.
                    batch.create_foreign_key(name, parent, ["user_id", column], ["user_id", "id"],
                                             deferrable=True, initially="DEFERRED")


def downgrade() -> None:
    grouped = defaultdict(list)
    for child, column, _parent in REFERENCES:
        grouped[child].append(column)
    for child, columns in grouped.items():
        with op.batch_alter_table(child) as batch:
            for column in columns:
                batch.drop_constraint(f"fk_{child}_seller_{column}", type_="foreignkey")
    for parent in PARENTS:
        with op.batch_alter_table(parent) as batch:
            batch.drop_constraint(f"uq_{parent}_seller_id", type_="unique")
