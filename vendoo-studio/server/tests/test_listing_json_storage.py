"""Upgrade real saved revisions without reparsing large JSON on every hosted read."""
import json
import uuid

from alembic import command
from sqlalchemy import JSON, inspect, select, text
from sqlalchemy.dialects.postgresql import JSONB

from test_seller_ownership import migration_engine  # noqa: F401
from vendoo_studio.database import json_value
from vendoo_studio.models.listing import Listing, ListingRevision
from vendoo_studio.services import schema_migrations
from vendoo_studio.services.hosted_database import SellerDatabaseSession


def test_saved_listing_upgrade_preserves_values_and_seller_security(migration_engine):  # noqa: F811
    engine = migration_engine
    config = schema_migrations.alembic_config(engine.url.render_as_string(hide_password=False))
    command.upgrade(config, "e5b1d7a3c920")
    owners = [str(uuid.uuid4()), str(uuid.uuid4())]
    body = {"sku": "coat-雪", "price": 42.75, "quantity": 3, "cost": None,
            "brand": "Chase", "category_path": ["Clothing", "Tops"],
            "marketplace_fields": {"large_form": "preserve this form " * 10000,
                                   "choices": [False, None, {"value": "é"}]}}
    with engine.begin() as connection:
        for index, owner in enumerate(owners):
            connection.execute(text("INSERT INTO conversations (id, title, user_id) VALUES (:id, 'Keep me', :owner)"),
                               {"id": f"item-{index}", "owner": owner})
            connection.execute(text(
                "INSERT INTO listing_revisions (id, conversation_id, user_id, listing_json, source, inventory_quantity) "
                "VALUES (:id, :conversation, :owner, :body, 'test', 3)"
            ), {"id": f"revision-{index}", "conversation": f"item-{index}", "owner": owner,
                "body": json.dumps(body)})
            connection.execute(text(
                "INSERT INTO listings (conversation_id, user_id, current_revision_id) "
                "VALUES (:conversation, :owner, :revision)"
            ), {"conversation": f"item-{index}", "owner": owner, "revision": f"revision-{index}"})
    command.upgrade(config, "head")
    stored_type = next(column["type"] for column in inspect(engine).get_columns("listing_revisions")
                       if column["name"] == "listing_json")
    assert isinstance(stored_type, JSONB if engine.dialect.name == "postgresql" else JSON)
    fields = ["sku", "price", "quantity", "cost", "brand", "category_path"]
    with engine.connect() as connection:
        assert connection.execute(select(ListingRevision.listing_json)).scalars().all() == [body, body]
        assert sorted(connection.execute(select(Listing.current_revision_id)).scalars()) == [
            "revision-0", "revision-1"]
        assert connection.execute(select(ListingRevision.inventory_quantity)).scalars().all() == [3, 3]
        assert connection.execute(select(*(json_value(ListingRevision.listing_json, key) for key in fields))) \
            .all() == [tuple(body[key] for key in fields)] * 2
    if engine.dialect.name == "postgresql":
        for index, owner in enumerate(owners):
            with SellerDatabaseSession(bind=engine, user_id=owner) as db:
                assert db.execute(select(ListingRevision.id, ListingRevision.listing_json)).all() == [
                    (f"revision-{index}", body)]
                assert db.execute(text("UPDATE listing_revisions SET source = 'changed' WHERE id = :other"),
                                  {"other": f"revision-{1-index}"}).rowcount == 0
                db.rollback()
