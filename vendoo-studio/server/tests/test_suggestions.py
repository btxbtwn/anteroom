"""Ranked inventory suggestions from local listing state."""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.services import listing_persistence
from vendoo_studio.database import Base, load_models
from vendoo_studio.models.fill_log import FillLogEntry  # noqa: F401
from vendoo_studio.models.registry import FieldRegistry  # noqa: F401
from vendoo_studio.models.listing_values import ValidationResult
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services.suggestions import list_suggestions


NOW = datetime(2026, 9, 20, tzinfo=UTC)


class SuggestionsTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        load_models()
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.convs = ConversationRepo(self.db)
        self.listings = ListingRepo(self.db)

    def tearDown(self):
        self.db.close()

    def _kinds(self) -> list[str]:
        return [card["kind"] for card in list_suggestions(self.db, now=NOW)]

    def _by_title(self, title: str) -> dict:
        cards = {card["title"]: card for card in list_suggestions(self.db, now=NOW)}
        self.assertIn(title, cards)
        return cards[title]

    def test_skips_sold_and_settled(self):
        sold = self.convs.create(title="Sold tee")
        self.convs.update_status(sold.id, "sold")
        settled = self.convs.create(title="Settled draft")
        listing_persistence.add_photo(self.convs.db, settled.id, "a.jpg", "a.jpg", "image/jpeg", 12)
        self.convs.settle(settled.id)
        self.assertEqual(self._kinds(), [])

    def test_failed_ranks_above_generate(self):
        failed = self.convs.create(title="Failed send")
        self.convs.update_status(failed.id, "failed")
        draft = self.convs.create(title="Needs generate")
        listing_persistence.add_photo(self.convs.db, draft.id, "a.jpg", "a.jpg", "image/jpeg", 12)
        kinds = self._kinds()
        self.assertEqual(kinds[0], "failed")
        self.assertIn("ready_to_generate", kinds)

    def test_photos_without_revision_are_ready_to_generate(self):
        conv = self.convs.create(title="New photos")
        listing_persistence.add_photo(self.convs.db, conv.id, "a.jpg", "a.jpg", "image/jpeg", 12)
        card = self._by_title("New photos")
        self.assertEqual(card["kind"], "ready_to_generate")
        self.assertIsNone(card["age_days"])
        self.assertTrue(card["cover_photo_url"].startswith("/api/photos/"))
        self.assertEqual(card["action"], "open")

    def test_validation_error_beats_stale(self):
        conv = self.convs.create(title="Broken active")
        conv.notes = json.dumps({
            "vendooMarketplaces": ["ebay"],
            "vendooDates": {"listed": "2026-01-01T00:00:00Z"},
        })
        self.db.commit()
        self.convs.update_status(conv.id, "active")
        listing_persistence.save_revision(self.listings.db, conv.id, {"title": "Broken active", "price": 20}, "test")
        listing = self.listings.get_current(conv.id)
        listing.validation_status = "error"
        self.db.commit()
        self.assertEqual(self._by_title("Broken active")["kind"], "fix_validation")

    def test_later_revision_drops_stale_validation_flag(self):
        conv = self.convs.create(title="Fixed draft")
        listing_persistence.save_revision(self.listings.db, conv.id, {"title": "Fixed draft", "price": 18}, "user_form")
        listing = self.listings.get_current(conv.id)
        listing.validation_status = "error"
        self.db.commit()
        with patch(
            "vendoo_studio.services.validation.validate_listing",
            return_value=ValidationResult(valid=True),
        ):
            listing_persistence.save_revision(self.listings.db, conv.id, {"title": "Fixed draft", "price": 19}, "chat")
        self.assertEqual(self.listings.get_current(conv.id).validation_status, "valid")
        self.assertEqual(self._by_title("Fixed draft")["kind"], "ready_to_review")

    def test_adding_a_photo_rechecks_validation_flag(self):
        conv = self.convs.create(title="Needs photos")
        listing_persistence.save_revision(self.listings.db, conv.id, {"title": "Needs photos", "price": 18}, "user_form")
        listing = self.listings.get_current(conv.id)
        listing.validation_status = "error"
        self.db.commit()
        with patch(
            "vendoo_studio.services.validation.validate_listing",
            return_value=ValidationResult(valid=True),
        ) as validate:
            listing_persistence.add_photo(self.convs.db, conv.id, "a.jpg", "a.jpg", "image/jpeg", 12)
        self.assertEqual(validate.call_args.args[1], 1)
        self.assertEqual(self.listings.get_current(conv.id).validation_status, "valid")

    def test_listing_suggestions_never_writes(self):
        conv = self.convs.create(title="Flagged")
        listing_persistence.save_revision(self.listings.db, conv.id, {"title": "Flagged", "price": 18}, "user_form")
        self.listings.get_current(conv.id).validation_status = "error"
        self.db.commit()
        with patch.object(self.db, "commit", side_effect=AssertionError("suggestions wrote")):
            self.assertEqual(self._by_title("Flagged")["kind"], "fix_validation")

    def test_stale_follows_the_oldest_live_marketplace(self):
        conv = self.convs.create(title="Bulk relisted")
        conv.notes = json.dumps({
            "vendooMarketplaces": ["ebay", "sellwild"],
            "vendooDates": {
                "listed": "2026-08-22T00:00:00Z",
                "listedByMarketplace": {
                    "ebay": "2026-06-22T00:00:00Z",
                    "sellwild": "2026-08-22T00:00:00Z",
                },
            },
        })
        self.convs.update_status(conv.id, "active")
        self.db.commit()
        cards = [card for card in list_suggestions(self.db, now=NOW) if card["kind"] == "stale_active"]
        self.assertEqual([card["age_days"] for card in cards], [90])

    def test_stale_starts_at_sixty_days_and_older_ranks_first(self):
        thirty = self.convs.create(title="Thirty")
        fifty_nine = self.convs.create(title="FiftyNine")
        sixty = self.convs.create(title="Sixty")
        ninety = self.convs.create(title="Ninety")
        for conv, listed in (
            (thirty, "2026-08-21T00:00:00Z"),
            (fifty_nine, "2026-07-23T00:00:00Z"),
            (sixty, "2026-07-22T00:00:00Z"),
            (ninety, "2026-06-22T00:00:00Z"),
        ):
            conv.notes = json.dumps({
                "vendooMarketplaces": ["ebay"],
                "vendooDates": {"listed": listed},
            })
            self.convs.update_status(conv.id, "active")
        self.db.commit()
        cards = [card for card in list_suggestions(self.db, now=NOW) if card["kind"] == "stale_active"]
        self.assertEqual([card["title"] for card in cards], ["Ninety", "Sixty"])
        self.assertEqual([card["age_days"] for card in cards], [90, 60])
        self.assertIn("60 days", cards[-1]["reason"])
        self.assertIn("eBay", cards[-1]["reason"])
        # Nothing else to tell them apart: age alone breaks the tie.
        self.assertEqual(cards[0]["score"], cards[1]["score"])

    def _stale(self, title: str, listed: str, listing: dict | None = None):
        conv = self.convs.create(title=title)
        conv.notes = json.dumps({"vendooMarketplaces": ["ebay"], "vendooDates": {"listed": listed}})
        self.db.commit()
        self.convs.update_status(conv.id, "active")
        if listing is not None:
            listing_persistence.save_revision(self.listings.db, conv.id, {"title": title, "price": 20, **listing}, "test")
        return conv

    def _sold(self, title: str, sold: str, listing: dict):
        conv = self.convs.create(title=title)
        conv.notes = json.dumps({"vendooDates": {"listed": "2026-05-01T00:00:00Z", "sold": sold}})
        self.db.commit()
        self.convs.update_status(conv.id, "sold")
        listing_persistence.save_revision(self.listings.db, conv.id, {"title": title, "price": 20, **listing}, "test")

    def _stale_titles(self) -> list[str]:
        return [card["title"] for card in list_suggestions(self.db, now=NOW) if card["kind"] == "stale_active"]

    def test_stale_like_recent_sales_ranks_above_older(self):
        tops = "Clothing, Shoes & Accessories > Women > Women's Clothing > Tops"
        self._stale("Old jeans", "2026-03-01T00:00:00Z", {"brand": "Levi's", "category_path": "Jeans"})
        self._stale("Madewell top", "2026-07-01T00:00:00Z", {"brand": "Madewell", "category_path": tops})
        self._stale("Other top", "2026-06-01T00:00:00Z", {"brand": "J.Crew", "category_path": tops})
        self._sold("Sold Madewell", "2026-09-10T00:00:00Z", {"brand": "Madewell", "category_path": tops})
        self._sold("Sold top", "2026-09-01T00:00:00Z", {"brand": "Gap", "category_path": tops})
        # Too long ago to say what is selling now.
        self._sold("Sold jeans", "2026-05-01T00:00:00Z", {"brand": "Levi's", "category_path": "Jeans"})
        self.assertEqual(self._stale_titles(), ["Madewell top", "Other top", "Old jeans"])
        card = self._by_title("Madewell top")
        self.assertEqual(card["similar_sold"], 2)
        self.assertIn("2 like it sold in the last 60 days", card["reason"])
        self.assertEqual(self._by_title("Old jeans")["similar_sold"], 0)

    def test_in_season_ranks_first_and_off_season_last(self):
        # NOW is September: Fall and the Winter ahead are in demand.
        self._stale("Plain mug", "2026-03-01T00:00:00Z", {})
        self._stale("Linen shorts", "2026-02-01T00:00:00Z", {"category_path": "Shorts"})
        self._stale("Wool sweater", "2026-07-01T00:00:00Z", {})
        self._stale("Tagged jacket", "2026-07-10T00:00:00Z", {"ebay_specifics": {"season": ["Fall", "Winter"]}})
        self._stale("Any time tee", "2026-01-01T00:00:00Z", {
            "ebay_specifics": {"season": ["Spring", "Summer", "Fall", "Winter"]},
        })
        self.assertEqual(
            self._stale_titles(),
            ["Wool sweater", "Tagged jacket", "Any time tee", "Plain mug", "Linen shorts"],
        )
        self.assertEqual(self._by_title("Wool sweater")["season_fit"], "in")
        self.assertIn("In season now", self._by_title("Wool sweater")["reason"])
        self.assertEqual(self._by_title("Linen shorts")["season_fit"], "off")
        self.assertIsNone(self._by_title("Any time tee")["season_fit"])

    def test_stale_score_stays_below_fix_validation(self):
        tops = "Women > Tops"
        self._stale("Hot top", "2025-01-01T00:00:00Z", {
            "brand": "Madewell", "category_path": tops, "ebay_specifics": {"season": "Fall"},
        })
        for index in range(8):
            self._sold(f"Sold {index}", "2026-09-10T00:00:00Z", {"brand": "Madewell", "category_path": tops})
        broken = self.convs.create(title="Broken draft")
        listing_persistence.save_revision(self.listings.db, broken.id, {"title": "Broken draft", "price": 18}, "user_form")
        self.listings.get_current(broken.id).validation_status = "error"
        self.db.commit()
        self.assertEqual(self._kinds()[:2], ["fix_validation", "stale_active"])
        self.assertLess(self._by_title("Hot top")["score"], 300)

    def test_draft_with_revision_is_ready_to_review(self):
        conv = self.convs.create(title="Ready draft")
        listing_persistence.save_revision(self.listings.db, conv.id, {"title": "Ready draft", "price": 18}, "generation")
        card = self._by_title("Ready draft")
        self.assertEqual(card["kind"], "ready_to_review")

    def test_caps_at_twenty(self):
        for index in range(25):
            conv = self.convs.create(title=f"Fail {index:02d}")
            self.convs.update_status(conv.id, "failed")
        self.assertEqual(len(list_suggestions(self.db, now=NOW)), 20)


if __name__ == "__main__":
    unittest.main()
