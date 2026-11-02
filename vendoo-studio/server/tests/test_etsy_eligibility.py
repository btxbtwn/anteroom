"""Etsy takes vintage, handmade, supplies and digital items — never modern resale.

Etsy removed listings of modern resale under its Creativity Standards. One
check (``etsy_eligible``) decides it everywhere: Send leaves Etsy's form empty,
Update leaves it alone, relist reminders skip Etsy, and a read-only report
names what is still live there. Nothing delists.
"""

from __future__ import annotations

import json
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.services import listing_persistence
from vendoo_studio.database import Base, get_db, load_models
from vendoo_studio.main import app
from vendoo_studio.services.etsy_fields import etsy_eligible
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services.etsy_audit import etsy_not_allowed_live
from vendoo_studio.services.vendoo_item_build import build_vendoo_item, marketplaces_left_empty
from vendoo_studio.services.vendoo_item_shape import default_listing_section
from vendoo_studio.services.vendoo_item_update import apply_update_all, changed_fields
from vendoo_studio.services.vendoo_marketplace_fields import _etsy_when_code

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

    def test_handmade_supplies_and_digital_are_allowed(self):
        self.assertTrue(etsy_eligible(listing({**MODERN, "who_made": "I did"})))
        self.assertTrue(etsy_eligible(listing({**MODERN, "who_made": "collective"})))
        self.assertTrue(etsy_eligible(listing({**MODERN, "what_is": "A supply or tool to make things"})))
        self.assertTrue(etsy_eligible(listing({**MODERN, "listing_type": "Digital files"})))


class SendLeavesEtsyEmptyTest(unittest.TestCase):
    def test_modern_resale_gets_vendoos_untouched_etsy_form(self):
        item, unresolved = build_vendoo_item(listing(MODERN, etsy_specifics={**MODERN, "categoryPath": "Clothing > Tops"}))
        etsy = item["listings"]["etsy"]
        self.assertEqual(etsy, default_listing_section("etsy"))
        # Unstamped and uncategorised: Vendoo does not count it as filled out.
        self.assertEqual(etsy["dateCreated"], "")
        self.assertNotIn("categoryV2", etsy["overrides"])
        self.assertFalse([row for row in unresolved if "etsy" in row["field"]])
        # Everything else is sent as before.
        self.assertEqual(item["generalDetails"]["title"], "Johnson Motors Biker Tee")

    def test_vintage_still_fills_etsy(self):
        item, _ = build_vendoo_item(listing({**VINTAGE, "categoryPath": "Clothing > Tops"}))
        etsy = item["listings"]["etsy"]
        self.assertEqual(etsy["overrides"]["categoryV2"]["displayPath"], ["Clothing", "Tops"])
        self.assertEqual(etsy["marketplaceSpecifics"]["whenMade"], "1990s")
        self.assertEqual(etsy["marketplaceSpecifics"]["tags"], ["biker", "tee"])
        self.assertTrue(etsy["dateCreated"])

    def test_left_out_marketplaces(self):
        self.assertEqual(marketplaces_left_empty(listing(MODERN)), frozenset({"etsy"}))
        self.assertEqual(marketplaces_left_empty(listing(VINTAGE)), frozenset())


class PrepareSkipsEtsyTest(unittest.TestCase):
    """No Etsy field is gap-filled or warned about for a form Send leaves empty."""

    def prepare(self, source: dict):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from vendoo_studio.services import vendoo_create
        from vendoo_studio.services.vendoo_specifics import FieldSpec

        resolved = vendoo_create.ResolvedFields(
            paths={},
            listing=source,
            specifics={"ebay": {"Color": FieldSpec("Color")}, "etsy": {"Sleeve": FieldSpec("Sleeve", required=True)}},
            unresolved=[{"field": "category:etsy", "value": "Clothing"}, {"field": "category:ebay", "value": "Tops"}],
        )
        fill = AsyncMock(side_effect=lambda listing, specifics, provider, evidence="": (listing, []))
        with patch.object(vendoo_create, "resolve_listing_fields", AsyncMock(return_value=resolved)), \
             patch.object(vendoo_create, "fill_listing_specifics", fill):
            _, specifics, _, unresolved, _ = asyncio.run(
                vendoo_create.prepare_listing_fields_for_vendoo(SimpleNamespace(id=None), source)
            )
        return specifics, unresolved

    def test_modern_resale_drops_etsy_fields_and_warnings(self):
        specifics, unresolved = self.prepare(listing(MODERN))
        self.assertEqual(sorted(specifics), ["ebay"])
        self.assertEqual([row["field"] for row in unresolved], ["category:ebay"])

    def test_vintage_keeps_them(self):
        specifics, unresolved = self.prepare(listing(VINTAGE))
        self.assertEqual(sorted(specifics), ["ebay", "etsy"])
        self.assertEqual(len(unresolved), 2)


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


class UpdateLeavesEtsyAloneTest(unittest.TestCase):
    def current(self) -> dict:
        return {
            "generalDetails": {"title": "Old title", "price": "20"},
            "listings": {
                "ebay": {"marketplaceID": "ebay", "overrides": {"title": "Old title"}},
                "etsy": {
                    "marketplaceID": "etsy",
                    "status": {"listed": True},
                    "overrides": {"title": "Old title", "price": "20"},
                    "marketplaceSpecifics": {"whoMade": "someone_else", "whenMade": "2010_2019", "tags": ["old"]},
                },
            },
        }

    def test_no_etsy_path_is_written_for_modern_resale(self):
        source = listing(MODERN, title="New title")
        desired, _ = build_vendoo_item(source)
        current = self.current()
        apply_update_all(current, desired, leave=marketplaces_left_empty(source))
        updates = changed_fields(current, desired)
        self.assertIn("listings.ebay.overrides.title", updates)
        self.assertFalse([path for path in updates if path.startswith("listings.etsy.")], updates)

    def test_vintage_updates_etsy_as_before(self):
        source = listing(VINTAGE, title="New title")
        desired, _ = build_vendoo_item(source)
        current = self.current()
        apply_update_all(current, desired, leave=marketplaces_left_empty(source))
        updates = changed_fields(current, desired)
        self.assertEqual(updates["listings.etsy.overrides.title"], "New title")


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

    def add(self, title: str, etsy: dict, notes: dict) -> str:
        conv = self.convs.create(title=title, notes=json.dumps(notes))
        listing_persistence.save_revision(self.listings.db, conv.id, listing(etsy, title=title), "test")
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


if __name__ == "__main__":
    unittest.main()
