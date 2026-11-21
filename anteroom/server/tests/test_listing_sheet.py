"""Cached marketplace sheet endpoint (`GET /api/listing-sheet`).

Returns display cells, readiness, and missing/rejected flags for one marketplace
across up to 50 listings without calling any live marketplace API or Chrome.
"""
from __future__ import annotations

import copy
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import jwks, signing_keys  # noqa: F401
from anteroom.database import Base, get_db
from anteroom.main import app
from anteroom.repositories.marketplace_listings import MarketplaceListingRepo
from anteroom.repositories.queries import ConversationRepo
from anteroom.services import listing_persistence
from anteroom.services.photo_store import LocalPhotoStore

LISTING = {
    "title": "Wool cardigan",
    "description": "Warm wool cardigan.",
    "brand": "Pendleton",
    "condition": "Good",
    "size": "M",
    "price": 32,
    "quantity": 1,
    "weight_oz": 16,
    "package_dimensions_in": "12x9x3",
    "direct_category_ids": {
        "poshmark": "01008c10d97b4e1245005764/07008c10d97b4e1245005764/33009813d97b4e3995005764",
        "mercari": "1934",
        "depop": "menswear_tshirts",
        "ebay": "15687",
        "ebay_path": "Clothing, Shoes & Accessories > Men > Men's Clothing > Shirts > T-Shirts",
        "etsy": "482",
        "etsy_path": "Clothing > Men's Clothing > Shirts & Tees",
    },
    "depop_specifics": {
        "parcelSize": "Medium",
        "colour": ["blue"],
    },
    "mercari_specifics": {
        "shipsFrom": "70125",
        "shippingClassId": "2550",
        "package": {"fitsShoebox": "no", "pounds": 1},
    },
    "etsy_specifics": {
        "whenMade": "2000_2006",
        "whoMade": "someone_else",
        "whatIsIt": "finished_product",
    },
}


class ListingSheetTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        store = LocalPhotoStore(directory)
        store.put("front.jpg", b"photo")
        self.enterContext(patch("anteroom.services.photo_store._store", store))
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)
        self.conv = ConversationRepo(self.db).create(title=LISTING["title"])
        listing_persistence.save_revision(self.db, self.conv.id, copy.deepcopy(LISTING), "model")
        listing_persistence.add_photo(self.db, self.conv.id, "front.jpg", "front.jpg", "image/jpeg", 1)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def test_depop_sheet_returns_display_cells_and_readiness(self):
        response = self.client.get("/api/listing-sheet", params={"marketplace": "depop", "ids": [self.conv.id, "missing"]})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["marketplace"], "depop")
        self.assertEqual(set(body["listings"]), {self.conv.id})
        row = body["listings"][self.conv.id]
        self.assertIsNotNone(row["revision_id"])
        self.assertFalse(row["needs_load"])
        self.assertEqual(row["cells"]["price"]["value"], "32")
        self.assertEqual(row["cells"]["depop_specifics.parcelSize"]["value"], "Medium")
        self.assertEqual(row["cells"]["depop_specifics.size"]["value"], "M")
        self.assertEqual(row["cells"]["depop_specifics.colour"]["value"], "Blue")
        self.assertTrue(row["cells"]["depop_specifics.parcelSize"]["editable"])

    def test_missing_and_rejected_fields_are_flagged_on_matching_cells(self):
        incomplete = copy.deepcopy(LISTING)
        incomplete.pop("depop_specifics", None)
        listing_persistence.save_revision(self.db, self.conv.id, incomplete, "user_form")
        MarketplaceListingRepo(self.db).save(self.conv.id, "depop", {
            "state": "not_saved",
            "error": "Depop rejected the listing.",
            "error_fields": [{"field": "depop_specifics.size", "message": "Depop didn't accept this size."}],
        })
        response = self.client.get("/api/listing-sheet", params={"marketplace": "depop", "ids": [self.conv.id]})
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()["listings"][self.conv.id]
        self.assertFalse(row["ready"])
        self.assertTrue(row["cells"]["depop_specifics.parcelSize"]["missing"])
        self.assertTrue(row["cells"]["depop_specifics.size"]["rejected"])
        self.assertIn("Depop didn't accept", row["cells"]["depop_specifics.size"]["error"])
        rejected_missing = [item for item in row["missing"] if item["rejected"]]
        self.assertTrue(any(item["label"] == "Size" for item in rejected_missing))

    def test_complex_cells_are_marked_not_inline_editable(self):
        posh = self.client.get("/api/listing-sheet", params={"marketplace": "poshmark", "ids": [self.conv.id]})
        self.assertEqual(posh.status_code, 200, posh.text)
        posh_row = posh.json()["listings"][self.conv.id]
        self.assertFalse(posh_row["cells"]["poshmark_specifics.inventoryRows"]["editable"])
        self.assertEqual(posh_row["cells"]["poshmark_specifics.inventoryRows"]["value"], "M")

        merc = self.client.get("/api/listing-sheet", params={"marketplace": "mercari", "ids": [self.conv.id]})
        self.assertEqual(merc.status_code, 200, merc.text)
        merc_row = merc.json()["listings"][self.conv.id]
        self.assertFalse(merc_row["cells"]["mercari_specifics.shippingClassId"]["editable"])

    def test_ebay_and_etsy_never_call_network_or_chrome_and_report_fallback_values(self):
        with patch("httpx.AsyncClient.request", side_effect=AssertionError("network call forbidden")), \
             patch("httpx.Client.request", side_effect=AssertionError("network call forbidden")), \
             patch("anteroom.services.browser_bridge.request", side_effect=AssertionError("chrome bridge forbidden")), \
             patch("anteroom.services.chrome_catalog.live_preview", side_effect=AssertionError("chrome preview forbidden")):
            ebay = self.client.get("/api/listing-sheet", params={"marketplace": "ebay", "ids": [self.conv.id]})
            self.assertEqual(ebay.status_code, 200, ebay.text)
            ebay_row = ebay.json()["listings"][self.conv.id]
            self.assertTrue(ebay_row["needs_load"])
            self.assertEqual(ebay_row["cells"]["price"]["value"], "32")
            self.assertIn("T-Shirts", ebay_row["cells"]["direct_category_ids.ebay"]["value"])
            self.assertEqual(ebay_row["cells"]["ebay_specifics.condition"]["value"], "Pre-owned - Good")
            self.assertEqual(ebay_row["cells"]["ebay_specifics.condition"]["raw"], "3000")

            etsy = self.client.get("/api/listing-sheet", params={"marketplace": "etsy", "ids": [self.conv.id]})
            self.assertEqual(etsy.status_code, 200, etsy.text)
            etsy_row = etsy.json()["listings"][self.conv.id]
            self.assertTrue(etsy_row["needs_load"])
            self.assertEqual(etsy_row["cells"]["price"]["value"], "32")
            self.assertTrue(etsy_row["cells"]["etsy_specifics.whenMade"]["value"])
            self.assertEqual(etsy_row["cells"]["etsy_specifics.whoMade"]["value"], "Another company or person")
            self.assertEqual(etsy_row["cells"]["etsy_specifics.whatIsIt"]["value"], "A finished product")

    def test_ebay_empty_condition_fallback_stays_blank(self):
        blank_conv = ConversationRepo(self.db).create(title="")
        listing_persistence.save_revision(self.db, blank_conv.id, {"direct_category_ids": {"ebay": "15687"}}, "user_form")
        ebay = self.client.get("/api/listing-sheet", params={"marketplace": "ebay", "ids": [blank_conv.id]})
        self.assertEqual(ebay.status_code, 200, ebay.text)
        cell = ebay.json()["listings"][blank_conv.id]["cells"]["ebay_specifics.condition"]
        self.assertEqual(cell["value"], "")
        self.assertEqual(cell["raw"], "")

    def test_per_marketplace_price_override_and_category_alias_rejection(self):
        priced = copy.deepcopy(LISTING)
        priced["depop_specifics"]["price"] = 27
        priced["seller_fields"] = ["depop_specifics.price"]
        listing_persistence.save_revision(self.db, self.conv.id, priced, "user_form")
        MarketplaceListingRepo(self.db).save(self.conv.id, "depop", {
            "state": "not_saved",
            "error": "Depop rejected the category.",
            "error_fields": [{"field": "marketplace_category_ids.depop", "message": "Pick a Depop category."}],
        })
        depop = self.client.get("/api/listing-sheet", params={"marketplace": "depop", "ids": [self.conv.id]})
        self.assertEqual(depop.status_code, 200, depop.text)
        depop_row = depop.json()["listings"][self.conv.id]
        self.assertEqual(depop_row["cells"]["price"]["value"], "27")
        self.assertTrue(depop_row["cells"]["direct_category_ids.depop"]["rejected"])
        self.assertIn("Pick a Depop category", depop_row["cells"]["direct_category_ids.depop"]["error"])

        ebay = self.client.get("/api/listing-sheet", params={"marketplace": "ebay", "ids": [self.conv.id]})
        self.assertEqual(ebay.status_code, 200, ebay.text)
        self.assertEqual(ebay.json()["listings"][self.conv.id]["cells"]["price"]["value"], "32")

    def test_sheet_columns_match_frontend_config(self):
        import re
        from pathlib import Path

        from anteroom.services.listing_sheet import SHEET_COLUMNS

        ts_path = Path(__file__).resolve().parents[2] / "src" / "components" / "marketplaceSheetConfig.ts"
        ts_source = ts_path.read_text(encoding="utf-8")
        block_match = re.search(
            r"export const MARKETPLACE_SHEET_COLUMNS[^=]*=\s*\{(.*?)\n\};",
            ts_source,
            re.DOTALL,
        )
        self.assertIsNotNone(block_match, "MARKETPLACE_SHEET_COLUMNS block not found")
        assert block_match is not None
        block = block_match.group(1)
        for marketplace, specs in SHEET_COLUMNS.items():
            market_match = re.search(rf"\b{marketplace}:\s*\[(.*?)\n  \],", block, re.DOTALL)
            self.assertIsNotNone(market_match, f"Missing {marketplace} columns in marketplaceSheetConfig.ts")
            assert market_match is not None
            ts_keys = re.findall(r'key:\s*"([^"]+)"', market_match.group(1))
            py_keys = [spec.key for spec in specs]
            self.assertEqual(ts_keys, py_keys, f"Column mismatch for {marketplace}")

    def test_rejects_more_than_max_listings_and_invalid_marketplace(self):
        too_many = self.client.get("/api/listing-sheet", params={"marketplace": "depop", "ids": [f"id-{i}" for i in range(51)]})
        self.assertEqual(too_many.status_code, 422)
        bad_market = self.client.get("/api/listing-sheet", params={"marketplace": "grailed", "ids": [self.conv.id]})
        self.assertEqual(bad_market.status_code, 422)


def test_hosted_sheet_reads_only_the_sellers_own_listings(cloud, signing_keys):  # noqa: F811
    client, _ = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    draft = client.post("/api/drafts", headers=alice, json={"title": "Vintage coat"}).json()
    saved = client.patch(f"/api/drafts/{draft['id']}", headers=alice, json={
        "title": "Vintage coat",
        "notes": "",
        "expected_updated_at": draft["updated_at"],
        "fields": {"price": 40, "brand": "Pendleton", "description": "Warm wool coat."},
    })
    assert saved.status_code == 200, saved.text

    mine = client.get("/api/listing-sheet", headers=alice, params={"marketplace": "depop", "ids": [draft["id"]]})
    assert mine.status_code == 200, mine.text
    assert set(mine.json()["listings"]) == {draft["id"]}
    assert mine.json()["listings"][draft["id"]]["cells"]["price"]["value"] == "40"

    theirs = client.get("/api/listing-sheet", headers=bob, params={"marketplace": "depop", "ids": [draft["id"]]})
    assert theirs.status_code == 200
    assert theirs.json()["listings"] == {}
