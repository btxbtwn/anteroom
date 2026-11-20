"""Small stored inventory fields, computed by the database from each revision."""
from sqlalchemy import JSON
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import ColumnElement


FIELDS = {key: key for key in ("sku", "price", "quantity", "cost", "brand", "category_path", "title", "type")}
FIELDS.update(season="ebay_specifics.season", material="ebay_specifics.material")


class InventoryProjection(ColumnElement):
    inherit_cache = True
    type = JSON()


@compiles(InventoryProjection)
def sqlite_projection(_expression, _compiler, **_kwargs):
    # json() preserves arrays, objects and booleans instead of turning them
    # into JSON strings or integers inside json_object().
    values = ", ".join(f"'{key}', json(listing_json -> '$.{path}')" for key, path in FIELDS.items())
    return f"json_object({values})"


@compiles(InventoryProjection, "postgresql")
def postgres_projection(_expression, _compiler, **_kwargs):
    # jsonb_set and #> are immutable, as a stored generated column requires.
    value = "'{}'::jsonb"
    for key, path in FIELDS.items():
        value = f"jsonb_set({value}, '{{{key}}}', coalesce(listing_json #> '{{{path.replace('.', ',')}}}', 'null'::jsonb))"
    return value
