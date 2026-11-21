"""Etsy takes vintage, handmade, supplies and digital items — never modern resale.

Etsy removed listings of modern resale under its Creativity Standards. One
check (``etsy_eligible``) decides it everywhere: Send leaves Etsy's form empty,
Update leaves it alone, relist reminders skip Etsy, and a read-only report
names what is still live there. Nothing delists.
"""

from __future__ import annotations

import json
import unittest
from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from test_seller_ownership import migration_engine  # noqa: F401

from anteroom.models.listing import Listing, ListingRevision
from anteroom.services import inventory_facets, listing_persistence, schema_migrations
from anteroom.database import Base, get_db, load_models
from anteroom.main import app
from anteroom.services.etsy_fields import (
    _canonical_etsy_when, etsy_eligible, etsy_when_is_unclear, etsy_when_options, etsy_when_raw, etsy_when_to_store,
    etsy_when_unclear_note, resolve_etsy_when,
)
from anteroom.repositories.queries import ConversationRepo, ListingRepo
from anteroom.services.etsy_audit import etsy_not_allowed_live
from anteroom.services.vendoo_marketplace_fields import _etsy_when_code

MODERN = {"who_made": "Another company or person", "what_is": "A finished product", "when_made": "2010 - 2019 (Recently)"}
VINTAGE = {"who_made": "Another company or person", "what_is": "A finished product", "when_made": "1990s (Vintage)"}


def listing(etsy: dict | None = None, **extra) -> dict:
    return {"title": "Johnson Motors Biker Tee", "price": 30, "tags": ["biker", "tee"], "etsy_specifics": etsy or {}, **extra}


class EtsyEligibleTest(unittest.TestCase):
    def test_modern_resale_is_not_allowed(self):
        self.assertFalse(etsy_eligible(listing(MODERN)))
        self.assertFalse(etsy_eligible(listing({**MODERN, "when_made": "2020 - 2026 (Recently)"})))
        self.assertFalse(etsy_eligible(listing({**MODERN, "when_made": "2007 - 2009 (Recently)"})))

    def test_made_to_order_resale_is_not_allowed(self):
        # Vendoo's code, as an import brings it back.
        self.assertFalse(etsy_eligible(listing({"who_made": "someone_else", "when_made": "made_to_order"})))

    def test_non_vintage_wording_is_not_treated_as_vintage(self):
        self.assertFalse(etsy_eligible(listing({"who_made": "someone_else", "when_made": "non-vintage"})))
        self.assertFalse(etsy_eligible(listing({"who_made": "someone_else", "when_made": "not vintage"})))

    def test_unknown_age_and_maker_is_treated_as_modern_resale(self):
        self.assertFalse(etsy_eligible(listing()))
        self.assertFalse(etsy_eligible({}))
        self.assertFalse(etsy_eligible(None))

    def test_vintage_is_allowed(self):
        self.assertTrue(etsy_eligible(listing(VINTAGE)))
        self.assertTrue(etsy_eligible(listing({"who_made": "someone_else", "when_made": "1980s"})))
        self.assertTrue(etsy_eligible(listing({"who_made": "someone_else", "when_made": "before_2007"})))
        self.assertTrue(etsy_eligible(listing({**MODERN, "when_made": "2000 - 2006 (Vintage)"})))

    def test_the_ebay_year_dates_an_item_with_no_etsy_answer(self):
        self.assertTrue(etsy_eligible(listing({"who_made": "someone_else"}, ebay_specifics={"yearManufactured": "1988"})))

    def test_the_ebay_form_year_dates_an_item_with_no_etsy_answer(self):
        # eBay's native form saves the aspect under its own name.
        dated = listing(ebay_specifics={"Year Manufactured": "1995"})
        self.assertTrue(etsy_eligible(dated))
        self.assertEqual(etsy_when_raw({}, dated), "1995")
        self.assertEqual(etsy_when_to_store("1995", dated), "1990s (Vintage)")
        # Any spelling, flat or nested, and a blank spelling does not hide a filled one.
        for ebay in ({"Year manufactured": "1988"}, {"category_specifics": {"Year Manufactured": "1988"}},
                     {"yearManufactured": "", "Year Manufactured": "1988"}):
            with self.subTest(ebay=ebay):
                self.assertEqual(etsy_when_raw({}, listing(ebay_specifics=ebay)), "1988")

    def test_handmade_supplies_and_digital_are_allowed(self):
        self.assertTrue(etsy_eligible(listing({**MODERN, "who_made": "I did"})))
        self.assertTrue(etsy_eligible(listing({**MODERN, "who_made": "collective"})))
        self.assertTrue(etsy_eligible(listing({**MODERN, "what_is": "A supply or tool to make things"})))
        self.assertTrue(etsy_eligible(listing({**MODERN, "listing_type": "Digital files"})))


# The cutoff is "today": pin it so these answers mean the same thing every year.
FROZEN_CLOCK = patch("anteroom.services.etsy_fields._first_recent_year",
                     lambda today=None: (today or date(2026, 11, 21)).year - 19)


@FROZEN_CLOCK
class EtsyUnclearWhenTest(unittest.TestCase):
    """A range on both sides of Etsy's 20-year vintage line dates nothing.

    eBay's "2000-2009" used to become "2000 - 2006 (Vintage)", so an item made
    in 2008 was told to Etsy as vintage. Etsy removes such listings.
    """

    UNCLEAR = ("2000-2009", "2000 - 2009", "2000s", "early 2000s", "1995 to 2010")

    def test_a_range_across_the_cutoff_is_left_blank(self):
        options = etsy_when_options()
        for text in self.UNCLEAR:
            with self.subTest(text=text):
                self.assertTrue(etsy_when_is_unclear(text, today=date(2026, 11, 21)))
                self.assertEqual(resolve_etsy_when(text, options), "")
                self.assertEqual(etsy_when_to_store(text, {}, options), "")
                self.assertEqual(_canonical_etsy_when(text), "")

    def test_clear_dates_and_etsy_ranges_still_resolve(self):
        options = etsy_when_options()
        cases = {
            "1988": "1980s (Vintage)",
            "1990s": "1990s (Vintage)",
            "2000 - 2006 (Vintage)": "2000 - 2006 (Vintage)",
            "2007 - 2009 (Recently)": "2007 - 2009 (Recently)",
            "Before 2007 (Vintage)": "Before 2007 (Vintage)",
            "2015": "2010 - 2019 (Recently)",
            "2010s": "2010 - 2019 (Recently)",
        }
        for text, label in cases.items():
            with self.subTest(text=text):
                self.assertFalse(etsy_when_is_unclear(text, today=date(2026, 11, 21)))
                self.assertEqual(resolve_etsy_when(text, options), label)

    def test_etsys_own_range_stays_an_answer_after_the_cutoff_moves(self):
        # In 2027 the cutoff is 2008, inside "2007 - 2009", but that label is Etsy's answer, not a guess.
        with patch("anteroom.services.etsy_fields._first_recent_year", lambda today=None: 2008):
            self.assertEqual(resolve_etsy_when("2007 - 2009 (Recently)", etsy_when_options()), "2007 - 2009 (Recently)")
            self.assertEqual(_canonical_etsy_when("2007 - 2009 (Recently)"), "2007 - 2009 (Recently)")
            self.assertEqual(etsy_when_unclear_note("2007 - 2009 (Recently)"), "")
            self.assertEqual(resolve_etsy_when("2000-2009", etsy_when_options()), "")

    def test_the_cutoff_moves_with_the_year(self):
        # Vintage is 20 years old: the first non-vintage year is 2007 in 2026, 2009 in 2028.
        self.assertTrue(etsy_when_is_unclear("2000-2007", today=date(2026, 1, 1)))
        self.assertFalse(etsy_when_is_unclear("2000-2008", today=date(2029, 1, 1)))
        self.assertTrue(etsy_when_is_unclear("2000-2009", today=date(2028, 1, 1)))

    def test_an_unclear_ebay_year_does_not_make_the_item_vintage(self):
        nascar = listing(ebay_specifics={"Year Manufactured": "2000-2009", "Vintage": "Yes"})
        self.assertFalse(etsy_eligible(nascar))
        # An Etsy answer that dates it still counts.
        self.assertTrue(etsy_eligible({**nascar, "etsy_specifics": {"when_made": "Before 2007 (Vintage)"}}))

    def test_the_etsy_tab_says_why_it_is_blank(self):
        note = etsy_when_unclear_note("2000-2009", today=date(2026, 11, 21))
        self.assertIn("2000-2009", note)
        self.assertIn("2007", note)
        self.assertEqual(etsy_when_unclear_note("1988", today=date(2026, 11, 21)), "")
        self.assertEqual(etsy_when_unclear_note("", today=date(2026, 11, 21)), "")


class EtsyWhenCodeTest(unittest.TestCase):
    def test_vintage_decades_keep_their_code(self):
        # These all used to be sent as 2020_2026, which tells Etsy the piece is new.
        cases = {
            "1990s (Vintage)": "1990s",
            "1980s": "1980s",
            "Before 2007 (Vintage)": "before_2007",
            "2000 - 2006 (Vintage)": "2000_2006",
            "1900 - 1909 (Vintage)": "1900s",
            "Before 1700 (Vintage)": "before_1700",
            "Made To Order (Not Yet Made)": "2010_2019",
            "2010 - 2019 (Recently)": "2010_2019",
            "2020_2026": "2020_2026",
            "": "2020_2026",
        }
        for label, code in cases.items():
            with self.subTest(label=label):
                self.assertEqual(_etsy_when_code(label), code)


class NotAllowedReportTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        load_models()
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.convs = ConversationRepo(self.db)
        self.listings = ListingRepo(self.db)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def add(self, title: str, etsy: dict, notes: dict, **extra) -> str:
        conv = self.convs.create(title=title, notes=json.dumps(notes))
        listing_persistence.save_revision(self.listings.db, conv.id, listing(etsy, title=title, **extra), "test")
        return conv.id

    def seed(self) -> str:
        live = {"vendooMarketplaces": ["ebay", "etsy"]}
        modern = self.add("Delta Pro Weight Jayhawks Tee", MODERN, {
            **live,
            "vendooListingUrls": {"etsy": "https://www.etsy.com/listing/1/jayhawks"},
            "vendooDates": {"listedByMarketplace": {"etsy": "2026-07-14T03:02:12+00:00"}},
        })
        self.add("Gitano 90s Rayon Pocket Tee", {"who_made": "someone_else", "when_made": "made_to_order"}, {
            **live, "vendooDates": {"listedByMarketplace": {"etsy": "2026-08-01T00:00:00+00:00"}},
        })
        self.add("Vintage Cardigan", VINTAGE, live)
        self.add("NASCAR Tee", {}, live, ebay_specifics={"Year Manufactured": "1995"})
        self.add("Sold on Etsy", MODERN, {**live, "vendooDates": {"soldByMarketplace": {"etsy": "2026-09-01"}}})
        self.add("Not on Etsy", MODERN, {"vendooMarketplaces": ["ebay"]})
        self.add("Never synced", MODERN, {})
        return modern

    def test_lists_only_unsold_live_etsy_items_etsy_does_not_allow(self):
        modern = self.seed()
        rows = etsy_not_allowed_live(self.db)
        self.assertEqual([row["title"] for row in rows], ["Delta Pro Weight Jayhawks Tee", "Gitano 90s Rayon Pocket Tee"])
        first, second = rows
        self.assertEqual(first["conversation_id"], modern)
        self.assertEqual(first["when_made"], "2010 - 2019 (Recently)")
        self.assertEqual(first["etsy_url"], "https://www.etsy.com/listing/1/jayhawks")
        self.assertFalse(first["title_says_vintage"])
        # Listed as made to order, but the title dates it: When Made may be the mistake.
        self.assertEqual(second["when_made"], "Made To Order (Not Yet Made)")
        self.assertTrue(second["title_says_vintage"])

    def test_year_aliases_agree_in_full_listing_facets_and_live_report(self):
        live = {"vendooMarketplaces": ["etsy"]}
        for canonical, eligible in (("2022", False), ("  ", True)):
            with self.subTest(canonical=canonical):
                specifics = {"year_manufactured": "1988", "yearManufactured": canonical}
                dated = listing(ebay_specifics=specifics)
                conv_id = self.add("Year aliases", {}, live, ebay_specifics=specifics)
                self.assertEqual(etsy_eligible(dated), eligible)
                self.assertEqual(inventory_facets.listing_facets(self.db)[conv_id]["etsy_eligible"], eligible)
                self.assertEqual(inventory_facets.listing_facet(self.db, conv_id)["etsy_eligible"], eligible)
                reported = {row["conversation_id"] for row in etsy_not_allowed_live(self.db)}
                self.assertEqual(conv_id in reported, not eligible)

    def test_route_is_read_only(self):
        self.seed()

        def override():
            yield self.db

        app.dependency_overrides[get_db] = override
        client = TestClient(app)
        before = {conv.id: conv.notes for conv in self.convs.list_all()}
        res = client.get("/api/etsy/not-allowed")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(len(res.json()["items"]), 2)
        self.assertEqual({conv.id: conv.notes for conv in self.convs.list_all()}, before)
        # There is no write side to this report.
        self.assertEqual(client.post("/api/etsy/not-allowed").status_code, 405)


def test_projected_etsy_eligibility_matches_stored_legacy_listings(migration_engine):  # noqa: F811
    schema_migrations.ensure_schema(migration_engine)
    cases = (
        ("Nested year", {"category_specifics": {"Year Manufactured": "1988"}}, True),
        ("Variant year", {"YeAr-Manufactured": "1988"}, True),
        ("Conflicting aliases", {"year_manufactured": "1988", "yearManufactured": "2022"}, False),
        ("Blank preferred year", {"yearManufactured": "  ", "category_specifics": {"Year manufactured": "1988"}}, True),
        ("Flat before nested", {"Year manufactured": "2022", "category_specifics": {"yearManufactured": "1988"}}, False),
        ("Malformed nested block", {"category_specifics": ["1988"]}, False),
    )
    with Session(migration_engine) as db:
        expected = {}
        for title, specifics, eligible in cases:
            conv = ConversationRepo(db).create(title=title, notes=json.dumps({"vendooMarketplaces": ["etsy"]}))
            # Store the historical payload without the current save normalizer.
            revision = ListingRevision(conversation_id=conv.id, source="test", listing_json=listing(
                title=title, ebay_specifics=specifics,
            ))
            db.add(revision)
            db.flush()
            db.add(Listing(conversation_id=conv.id, current_revision_id=revision.id))
            db.commit()
            assert etsy_eligible(revision.listing_json) is eligible
            expected[conv.id] = eligible

        facets = inventory_facets.listing_facets(db)
        assert {conv_id: facet["etsy_eligible"] for conv_id, facet in facets.items()} == expected
        for conv_id, eligible in expected.items():
            assert inventory_facets.listing_facet(db, conv_id)["etsy_eligible"] is eligible
        assert {row["conversation_id"] for row in etsy_not_allowed_live(db)} == {
            conv_id for conv_id, eligible in expected.items() if not eligible
        }


if __name__ == "__main__":
    unittest.main()
