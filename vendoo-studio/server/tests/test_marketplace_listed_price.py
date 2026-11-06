"""The live price per marketplace that the inventory table shows."""
from datetime import UTC, datetime

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import MetaData, Table, select

from test_seller_ownership import migration_config, migration_engine  # noqa: F401
from vendoo_studio.database import Base
from vendoo_studio.models.marketplace_listing import MarketplaceListing


def test_upgrade_backfills_live_listings_with_the_price_they_were_sent(migration_engine):  # noqa: F811
    engine = migration_engine
    config = migration_config(engine)
    command.upgrade(config, "ca4d82e917f6")
    listing = {"title": "Coat", "price": 40, "poshmark_specifics": {"poshmarkPrice": 48},
               "ebay_specifics": {"price": 99}, "seller_fields": ["poshmark_specifics.poshmarkPrice"]}
    now = datetime.now(UTC).replace(tzinfo=None)
    with engine.begin() as connection:
        tables = {name: Table(name, MetaData(), autoload_with=connection)
                  for name in ("conversations", "listing_revisions", "marketplace_listings")}
        connection.execute(tables["conversations"].insert().values(id="item", title="Coat"))
        connection.execute(tables["listing_revisions"].insert().values(
            id="revision", conversation_id="item", source="test", listing_json=listing))
        rows = [("posh", "poshmark", "published", "revision"), ("ebay", "ebay", "pending_review", "revision"),
                ("mercari", "mercari", "draft", "revision"), ("depop", "depop", "published", None)]
        for row_id, marketplace, state, revision_id in rows:
            connection.execute(tables["marketplace_listings"].insert().values(
                id=row_id, conversation_id="item", marketplace=marketplace, state=state, revision_id=revision_id,
                created_at=now, updated_at=now))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        prices = dict(connection.execute(select(MarketplaceListing.id, MarketplaceListing.listed_price)).all())
        # Poshmark had the seller's own price; eBay's unlocked key was never sent.
        assert prices == {"posh": 48, "ebay": 40, "mercari": None, "depop": None}
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
