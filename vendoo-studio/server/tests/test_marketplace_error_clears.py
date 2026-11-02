"""A marketplace's "needs these fields" error clears once a save changes those fields."""
from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.services import listing_persistence
from vendoo_studio.database import Base
from vendoo_studio.models.fill_log import FillLogEntry  # noqa: F401
from vendoo_studio.models.marketplace_listing import MarketplaceListing  # noqa: F401
from vendoo_studio.models.registry import FieldRegistry  # noqa: F401
from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo

CATEGORY = {"field": "vinted_specifics.category", "message": "Vinted needs a category Vinted offers"}
TITLE = {"field": "title", "message": "Vinted needs a title"}


class MarketplaceErrorClearsTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.conv = ConversationRepo(self.db).create(title="Trousers")
        self.listings = ListingRepo(self.db)
        self.records = MarketplaceListingRepo(self.db)
        listing_persistence.save_revision(self.listings.db, self.conv.id, {"title": "Trousers", "vinted_specifics": {
            "category": "Women > Activewear > Pants"}}, "test")

    def tearDown(self):
        self.db.close()

    def _fail(self, fields: list[dict], state: str = "not_saved") -> None:
        self.records.save(self.conv.id, "vinted", {"state": state, "error": "Vinted needs a category Vinted offers.",
                                                   "error_code": "missing_fields", "error_fields": fields})

    def _save(self, listing: dict) -> dict:
        listing_persistence.save_revision(self.listings.db, self.conv.id, listing, "model_refinement")
        return self.records.get(self.conv.id, "vinted")

    def test_changing_the_named_field_clears_the_error(self):
        self._fail([CATEGORY])
        record = self._save({"title": "Trousers", "vinted_specifics": {
            "category": "Women > Clothing > Pants & leggings > Tailored pants"}})
        self.assertNotIn("error", record)
        self.assertNotIn("error_code", record)
        self.assertNotIn("error_fields", record)
        self.assertEqual(record["state"], "not_saved")

    def test_an_unrelated_change_keeps_the_error(self):
        self._fail([CATEGORY])
        record = self._save({"title": "Gray trousers", "vinted_specifics": {"category": "Women > Activewear > Pants"}})
        self.assertEqual(record["error_code"], "missing_fields")

    def test_only_the_fixed_field_leaves_the_error(self):
        self._fail([CATEGORY, TITLE])
        record = self._save({"title": "Trousers", "vinted_specifics": {"category": "Women > Clothing > Pants"}})
        self.assertEqual(record["error_fields"], [TITLE])
        self.assertEqual(record["error"], "Vinted needs a title.")

    def test_a_marketplace_copy_of_a_top_level_field_counts(self):
        self._fail([TITLE])
        record = self._save({"title": "Trousers", "vinted_specifics": {
            "category": "Women > Activewear > Pants", "title": "Vinted trousers"}})
        self.assertNotIn("error", record)

    def test_an_unsettled_action_keeps_its_error(self):
        self._fail([CATEGORY], state="unknown")
        record = self._save({"title": "Trousers", "vinted_specifics": {"category": "Women > Clothing > Pants"}})
        self.assertEqual(record["error_code"], "missing_fields")


if __name__ == "__main__":
    unittest.main()
