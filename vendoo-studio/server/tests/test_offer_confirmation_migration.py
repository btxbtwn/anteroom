"""Upgrade existing offer history without inventing receipts or enabling retries."""
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import select, text

from test_seller_ownership import migration_config, migration_engine  # noqa: F401
from vendoo_studio.database import Base
from vendoo_studio.models.listing_promotion import ListingPromotion


def test_upgrade_preserves_send_gates_and_marks_old_evidence_unknown(migration_engine):  # noqa: F811
    engine = migration_engine
    config = migration_config(engine)
    command.upgrade(config, "b1f4c7e2d903")
    with engine.begin() as connection:
        for state, action in [("done", "offer"), ("unknown", "offer"), ("done", "share")]:
            connection.execute(text("INSERT INTO listing_promotions "
                                    "(id, batch_id, marketplace, action, listing_id, state, created_at, expires_at, note) "
                                    "VALUES (:id, 'batch', 'poshmark', :action, 'listing', :state, "
                                    "'2026-11-01', '2026-11-02', 'old note')"),
                               {"id": f"{state}-{action}", "state": state, "action": action})
    command.upgrade(config, "head")
    with engine.connect() as connection:
        rows = {row.id: row for row in connection.execute(select(ListingPromotion.__table__)).mappings()}
        assert rows["done-offer"].confirmation == "legacy_unverified"
        assert rows["done-offer"].recipient_count is None
        assert "not verified" in rows["done-offer"].note
        assert rows["done-offer"].state == "done"
        assert rows["unknown-offer"].state == "unknown"
        assert rows["unknown-offer"].note == "old note"
        assert rows["done-share"].note == "old note"
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
