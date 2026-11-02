"""Drafts that relied on Vendoo's category keep it on each marketplace tab's own key."""
from importlib import import_module

from alembic import command
from sqlalchemy.orm import Session

from test_seller_ownership import migration_config, migration_engine  # noqa: F401
from vendoo_studio.services import listing_persistence
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services.native_category_match import native_rows

backfill = import_module("vendoo_studio.migrations.versions.b4e9d2c7a813_marketplace_tabs_keep_their_own_category")
POSHMARK_TEES = "01008c10d97b4e1245005764/07008c10d97b4e1245005764/33009813d97b4e3995005764"
VENDOO = {
    "marketplace_category_ids": {"ebay": "15687", "etsy": "482", "depop": "menswear_tshirts", "mercari": "303",
                                 "poshmark": "a" * 24},
    "marketplace_categories": {"ebay": "Clothing > Men > T-Shirts", "poshmark": "Men > Shirts > Tees - Short Sleeve"},
}


def test_each_tab_takes_the_category_it_showed():
    listing = {"title": "Tee", **VENDOO}
    assert backfill.adopt(listing, native_rows())
    assert listing["direct_category_ids"] == {
        "ebay": "15687", "ebay_path": "Clothing > Men > T-Shirts", "etsy": "482",
        "depop": "menswear_tshirts", "mercari": "303", "poshmark": POSHMARK_TEES,
    }


def test_a_tabs_own_category_and_unknown_ids_are_left_alone():
    listing = {
        "marketplace_category_ids": {"ebay": "15687", "depop": "menswear_not-a-type", "mercari": "1"},
        "marketplace_categories": {"poshmark": "Men > Not A Category"},
        "direct_category_ids": {"ebay": "57988"},
    }
    assert not backfill.adopt(listing, native_rows())
    assert listing["direct_category_ids"] == {"ebay": "57988"}
    # Poshmark's own list spells a category without a sub-category as "None".
    none = {"marketplace_categories": {"poshmark": "Men > Shirts"}}
    assert backfill.adopt(none, native_rows())
    assert none["direct_category_ids"] == {"poshmark": "01008c10d97b4e1245005764/07008c10d97b4e1245005764"}


def test_upgrade_fills_the_current_revision(migration_engine):  # noqa: F811
    config = migration_config(migration_engine)
    command.upgrade(config, backfill.down_revision)
    with Session(migration_engine) as db:
        conv = ConversationRepo(db).create(title="Tee")
        old = listing_persistence.save_revision(db, conv.id, {"title": "Tee"}, "model")
        current = listing_persistence.save_revision(db, conv.id, {"title": "Tee", **VENDOO}, "model", parent_revision_id=old.id)
        ids = (old.id, current.id)
    command.upgrade(config, "head")
    with Session(migration_engine) as db:
        repo = ListingRepo(db)
        assert "direct_category_ids" not in repo.get_revision(ids[0]).listing_json
        assert repo.get_revision(ids[1]).listing_json["direct_category_ids"]["depop"] == "menswear_tshirts"
