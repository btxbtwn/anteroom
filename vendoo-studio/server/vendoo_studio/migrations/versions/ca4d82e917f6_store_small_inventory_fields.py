"""Store small inventory fields separately from marketplace forms.

Revision ID: ca4d82e917f6
Revises: b6e2c4a81f09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "ca4d82e917f6"
down_revision = "b6e2c4a81f09"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    if "inventory_fields" in {column["name"] for column in sa.inspect(connection).get_columns("listing_revisions")}:
        return
    # Freeze this revision's projection; do not import the live model compiler.
    fields = {key: key for key in ("sku", "price", "quantity", "cost", "brand", "category_path", "title", "type")}
    fields.update(season="ebay_specifics.season", material="ebay_specifics.material")
    if connection.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
        expression = "'{}'::jsonb"
        for key, path in fields.items():
            expression = f"jsonb_set({expression}, '{{{key}}}', coalesce(listing_json #> '{{{path.replace('.', ',')}}}', 'null'::jsonb))"
        op.add_column("listing_revisions", sa.Column("inventory_fields", JSONB(),
                      sa.Computed(expression, persisted=True), nullable=False))
    else:
        values = ", ".join(f"'{key}', json(listing_json -> '$.{path}')" for key, path in fields.items())
        # SQLite cannot add a stored generated column with ALTER TABLE.
        with op.batch_alter_table("listing_revisions", recreate="always") as batch:
            batch.add_column(sa.Column("inventory_fields", sa.JSON(),
                             sa.Computed(f"json_object({values})", persisted=True), nullable=False))


def downgrade():
    with op.batch_alter_table("listing_revisions") as batch:
        batch.drop_column("inventory_fields")
