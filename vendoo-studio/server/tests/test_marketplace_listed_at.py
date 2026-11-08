"""When each direct listing went up, so a relist makes the item fresh again."""
from datetime import datetime

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import MetaData, Table, select

from test_seller_ownership import migration_config, migration_engine  # noqa: F401
from vendoo_studio.database import Base
from vendoo_studio.models.marketplace_listing import MarketplaceListing


def test_upgrade_backfills_live_listings_with_their_last_list(migration_engine):  # noqa: F811
    engine = migration_engine
    config = migration_config(engine)
    command.upgrade(config, "a7d3e5c1b9f4")
    first, relisted, updated = datetime(2026, 7, 1), datetime(2026, 11, 8, 4, 43), datetime(2026, 11, 9)
    with engine.begin() as connection:
        tables = {name: Table(name, MetaData(), autoload_with=connection)
                  for name in ("conversations", "jobs", "marketplace_listings")}
        connection.execute(tables["conversations"].insert().values(id="item", title="Coat"))
        jobs = [("j1", "native_poshmark_list", "completed", first), ("j2", "native_poshmark_list", "completed", relisted),
                ("j3", "native_poshmark_update", "completed", updated), ("j4", "native_depop_publish", "failed", relisted),
                ("j5", "native_ebay_list", "completed", relisted)]
        for job_id, step, status, when in jobs:
            connection.execute(tables["jobs"].insert().values(
                id=job_id, conversation_id="item", approved_revision_id="revision", listing_snapshot={},
                status=status, current_step=step, created_at=when, updated_at=when))
        for row_id, marketplace, state in [("posh", "poshmark", "published"), ("depop", "depop", "published"),
                                           ("ebay", "ebay", "draft")]:
            connection.execute(tables["marketplace_listings"].insert().values(
                id=row_id, conversation_id="item", marketplace=marketplace, state=state,
                created_at=first, updated_at=first))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        listed = dict(connection.execute(select(MarketplaceListing.id, MarketplaceListing.listed_at)).all())
        # Poshmark's last List, not its Update; Depop's List failed; eBay is not live.
        assert listed == {"posh": relisted, "depop": None, "ebay": None}
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
