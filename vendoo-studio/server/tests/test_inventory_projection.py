from alembic import command
from sqlalchemy import MetaData, Table, event, select
from sqlalchemy.orm import Session

from test_seller_ownership import migration_config, migration_engine  # noqa: F401
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.listing import Listing, ListingRevision
from vendoo_studio.models.marketplace_listing import MarketplaceListing
from vendoo_studio.routes.hosted_workspace import inventory_response
from vendoo_studio.services import schema_migrations
from vendoo_studio.services.account_import import _row
from vendoo_studio.services.inventory_analytics import load_rows
from vendoo_studio.services.suggestions import _listing_rows


def test_inventory_reads_do_not_extract_large_marketplace_forms(migration_engine):  # noqa: F811
    engine = migration_engine
    schema_migrations.ensure_schema(engine)
    with Session(engine) as db:
        db.add(Conversation(id="item", title="Coat"))
        db.flush()
        db.add(ListingRevision(id="revision", conversation_id="item", source="test", listing_json={
            "sku": "coat-1", "price": 42, "quantity": 2, "cost": 5,
            "brand": "Example", "category_path": ["Men", "Coats"], "title": "Wool coat", "type": "Coat",
            "ebay_specifics": {"season": ["Winter"], "material": ["Wool"]},
            "marketplace_forms": {"unused": "large form " * 10000},
        }))
        db.flush()
        db.add(Listing(conversation_id="item", current_revision_id="revision"))
        db.add(MarketplaceListing(conversation_id="item", marketplace="ebay", state="published",
                                  listing_url="https://www.ebay.com/itm/1"))
        db.add(MarketplaceListing(conversation_id="item", marketplace="mercari", state="not_saved",
                                  error="Rejected", error_fields=[{"field": "size", "message": "Required"}]))
        db.commit()
        reads = []

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT") and "listing_revisions" in statement:
                reads.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        try:
            suggestion = _listing_rows(db)[0]
            analytics = load_rows(db)[0]
            summary = inventory_response(db)[0]
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        assert suggestion[3:] == ("Example", ["Men", "Coats"], ["Winter"], "Wool coat", "Coat", ["Wool"])
        assert analytics.price == 42 and analytics.cost == 5
        assert summary["sku"] == "coat-1" and summary["price"] == 42 and summary["quantity"] == 2
        assert summary["direct_marketplaces"] == ["ebay"]
        assert summary["direct_records"] == {
            "ebay": {"state": "published", "listing_url": "https://www.ebay.com/itm/1", "failed": False, "fields": 0, "price": None},
            "mercari": {"state": "not_saved", "listing_url": None, "failed": True, "fields": 1, "price": None},
        }
        assert reads
        assert not any("listing_json" in query for query in reads)


def test_upgrade_computes_existing_fields_and_sql_writes_keep_them_current(migration_engine):  # noqa: F811
    engine = migration_engine
    config = migration_config(engine)
    command.upgrade(config, "b6e2c4a81f09")
    payload = {"sku": "coat-1", "price": 42.5, "quantity": 2, "cost": 0, "brand": "Example",
               "category_path": ["Men", "Coats"], "title": "Wool coat", "type": "Coat",
               "ebay_specifics": {"season": ["Winter"], "material": ["Wool"]},
               "marketplace_forms": {"unused": "form " * 10000}}
    expected = {"sku": "coat-1", "price": 42.5, "quantity": 2, "cost": 0, "brand": "Example",
                "category_path": ["Men", "Coats"], "title": "Wool coat", "type": "Coat",
                "season": ["Winter"], "material": ["Wool"]}
    with engine.begin() as connection:
        conversations = Table("conversations", MetaData(), autoload_with=connection)
        revisions = Table("listing_revisions", MetaData(), autoload_with=connection)
        connection.execute(conversations.insert().values(id="item", title="Coat"))
        connection.execute(revisions.insert().values(id="revision", conversation_id="item", source="test", listing_json=payload))
    command.upgrade(config, "head")
    with engine.begin() as connection:
        assert connection.scalar(select(ListingRevision.inventory_fields)) == expected
        assert connection.scalar(select(ListingRevision.listing_json)) == payload
        # Direct SQL must update the projection too, independently of ORM hooks.
        connection.execute(ListingRevision.__table__.update().values(listing_json={
            "price": None, "quantity": 0, "title": "Changed", "brand": True,
            "category_path": '["Men", "Coats"]', "ebay_specifics": None,
        }))
        updated = connection.scalar(select(ListingRevision.inventory_fields))
        assert updated == {"sku": None, "price": None, "quantity": 0, "cost": None, "brand": True,
                           "category_path": '["Men", "Coats"]', "title": "Changed", "type": None,
                           "season": None, "material": None}
    # A Mac export includes generated columns; the server recomputes rather
    # than trying to insert the exported generated value.
    raw = {"id": "other", "conversation_id": "item", "source": "test", "listing_json": payload,
           "inventory_fields": {"price": 999}}
    prepared = _row(ListingRevision.__table__, raw, "11111111-1111-1111-1111-111111111111")
    assert "inventory_fields" not in prepared
    assert prepared["listing_json"] == payload
