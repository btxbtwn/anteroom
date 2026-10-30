"""Upgrade existing approvals without silently expanding their approved scope."""
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import select

from test_seller_ownership import migration_config, migration_engine  # noqa: F401
from vendoo_studio.database import Base
from vendoo_studio.models.listing_promotion import MarketplaceOfferFloor
from vendoo_studio.models.promotion_automation import PromotionAutomation


def test_upgrade_preserves_minimums_and_pauses_only_enabled_offer_rules(migration_engine):  # noqa: F811
    engine = migration_engine
    config = migration_config(engine)
    command.upgrade(config, "84bc2a03cbad")
    table = PromotionAutomation.__table__
    with engine.begin() as connection:
        for name, action, enabled in [("offers", "offer", True), ("off", "offer", False), ("sharing", "share", True)]:
            connection.execute(table.insert().values(id=name, name=name, marketplace="poshmark", action=action,
                                                     enabled=enabled, discount_percent=10))
        connection.execute(MarketplaceOfferFloor.__table__.insert().values(marketplace="poshmark", listing_id="keep", floor_cents=2500))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        rows = {row.id: row for row in connection.execute(select(table)).mappings()}
        assert not rows["offers"].enabled
        assert "minimum price" in rows["offers"].paused_reason
        assert rows["offers"].next_run_at is None
        assert not rows["off"].enabled
        assert rows["sharing"].enabled
        assert all(row.minimum_price_cents is None for row in rows.values())
        assert connection.scalar(select(MarketplaceOfferFloor.floor_cents)) == 2500
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
