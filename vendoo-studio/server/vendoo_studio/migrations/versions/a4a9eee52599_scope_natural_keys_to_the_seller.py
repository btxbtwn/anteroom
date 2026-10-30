"""Scope natural keys to the seller without replacing any records.

Revision ID: a4a9eee52599
Revises: 9664651bec7c
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "a4a9eee52599"
down_revision = "9664651bec7c"
branch_labels = None
depends_on = None

# Frozen here: migrations must not depend on the current application's models.
UNIQUE_KEYS = (
    ("category_schemas", ("general_path", "marketplace"), "uq_category_schemas_seller"),
    ("category_field_schemas", ("marketplace", "category_id"), "uq_category_field_schemas_seller"),
    ("category_nodes", ("marketplace", "path"), "uq_category_nodes_seller"),
    ("diagnostic_runs", ("observation_id",), "uq_diagnostic_runs_seller_observation"),
    ("ebay_offer_rules", ("account_id", "listing_id"), "uq_ebay_offer_rule_listing"),
    ("field_registry", ("marketplace", "category_path", "normalized_label"), "uq_field_registry_identity"),
    ("marketplace_offer_floors", ("marketplace", "listing_id"), "uq_marketplace_offer_floor_listing"),
    ("marketplace_sales", ("marketplace", "order_id", "line_id"), "uq_marketplace_sales_order_line"),
)
PRIMARY_KEYS = (("photo_fingerprints", "checksum"), ("listing_copy_receipts", "request_id"))
NAMING = {"uq": "uq_%(table_name)s_%(column_0_name)s", "pk": "pk_%(table_name)s"}


def upgrade() -> None:
    connection = op.get_bind()
    for table, columns, name in UNIQUE_KEYS:
        constraints = sa.inspect(connection).get_unique_constraints(table)
        old = [constraint for constraint in constraints if tuple(constraint["column_names"]) == columns]
        scoped = any(tuple(constraint["column_names"]) == ("user_id", *columns) for constraint in constraints)
        if not old and scoped:
            continue  # A newly created table during pre-Alembic adoption.
        with op.batch_alter_table(table, naming_convention=NAMING) as batch:
            for constraint in old:
                batch.drop_constraint(constraint["name"] or f"uq_{table}_{columns[0]}", type_="unique")
            if not scoped:
                batch.create_unique_constraint(name, ["user_id", *columns])

    for table, column in PRIMARY_KEYS:
        primary = sa.inspect(connection).get_pk_constraint(table)
        if primary["constrained_columns"] == ["user_id", column]:
            continue
        with op.batch_alter_table(table, naming_convention=NAMING) as batch:
            batch.drop_constraint(primary["name"] or f"pk_{table}", type_="primary")
            batch.create_primary_key(f"pk_{table}", ["user_id", column])

    index = next(index for index in sa.inspect(connection).get_indexes("ebay_actions")
                 if index["name"] == "uq_ebay_action_in_flight")
    if index["column_names"] != ["user_id", "state"]:
        op.drop_index("uq_ebay_action_in_flight", table_name="ebay_actions")
        op.create_index("uq_ebay_action_in_flight", "ebay_actions", ["user_id", "state"], unique=True,
                        sqlite_where=sa.text("state = 'sending'"), postgresql_where=sa.text("state = 'sending'"))


def downgrade() -> None:
    # Two accounts can now have the same keys. Restoring global constraints
    # would require discarding seller records; use a pre-upgrade backup instead.
    raise RuntimeError("Seller-scoped keys cannot be downgraded; restore a pre-upgrade backup")
