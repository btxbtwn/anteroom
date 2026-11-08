"""Readiness counts the fields a marketplace still needs, before anything is listed.

A marketplace tab and an inventory cell say "Needs N fields". N is the
required fields of that marketplace's form the listing leaves empty, together
with the fields the marketplace rejected on the last List, each field once.
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
from vendoo_studio.database import Base, get_db
from vendoo_studio.main import app
from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
from vendoo_studio.repositories.queries import ConversationRepo
from vendoo_studio.services import listing_persistence
from vendoo_studio.services import native_marketplaces as native
from vendoo_studio.services.listing_readiness import label, missing
from vendoo_studio.services.photo_store import LocalPhotoStore

# Generated copy with a category everywhere, but no price yet and no Depop package size.
LISTING = {"title": "Wool cardigan", "description": "Warm wool cardigan.", "brand": "Pendleton",
           "size": "M", "weight_oz": 16, "package_dimensions_in": "12x9x3",
           "direct_category_ids": {"poshmark": "01008c10d97b4e1245005764/07008c10d97b4e1245005764/33009813d97b4e3995005764",
                                   "mercari": "1934", "depop": "menswear_tshirts"},
           "mercari_specifics": {"shipsFrom": "70125", "shippingClassId": "2550",
                                 "package": {"fitsShoebox": "no", "pounds": 1}}}


def labels(result: dict, marketplace: str) -> list[str]:
    return [item["label"] for item in result["marketplaces"][marketplace]["missing"]]


class MissingTest(unittest.TestCase):
    def test_blocking_and_empty_required_count_but_advice_does_not(self):
        gaps = [
            {"field": "etsy_specifics.when_made", "message": "Pick when it was made.", "blocking": True},
            {"field": "poshmark_specifics.condition", "message": "Condition: Required field is empty.",
             "blocking": False, "required": True, "label": "Condition"},
            {"field": "ebay_specifics.returnPolicyId", "message": "Uses the account's own choice.", "blocking": False},
            {"field": "marketplace_photos.ebay", "message": "Two photos are left off.", "blocking": False},
        ]
        self.assertEqual([item["label"] for item in missing(gaps)], ["When made", "Condition"])

    def test_a_rejected_field_counts_once_with_the_same_empty_field(self):
        gaps = [{"field": "mercari_specifics.nativeSizeId", "message": "Size: Required field is empty.",
                 "blocking": False, "required": True, "label": "Size"}]
        rejected = [{"field": "mercari_specifics.size", "message": "Please select a valid size."},
                    {"field": "mercari_specifics.brand", "message": "Brand is not allowed."}]
        self.assertEqual([item["label"] for item in missing(gaps, rejected)], ["Size", "Brand"])

    def test_the_same_field_named_twice_counts_once(self):
        gaps = [{"field": "mercari_specifics.title", "message": "Title is too long.", "blocking": True},
                {"field": "title", "message": "Title is empty.", "blocking": True}]
        self.assertEqual(len(missing(gaps, [{"field": "title", "message": "Rejected."}])), 1)

    def test_a_form_label_reads_without_its_trailing_colon(self):
        # Mercari's sell page labels the row "Ships from:"; the seller reads "Ships from".
        gaps = [{"field": "mercari_specifics.shipsFrom", "message": "Ships from: Required field is empty.",
                 "blocking": False, "required": True, "label": "Ships from:"},
                {"field": "mercari_specifics.prepaid", "message": "Required.", "blocking": True,
                 "label": "Will your item ship with a prepaid label?"}]
        self.assertEqual([item["label"] for item in missing(gaps)],
                         ["Ships from", "Will your item ship with a prepaid label?"])

    def test_field_names_read_as_the_form_says_them(self):
        self.assertEqual(label("direct_category_ids.mercari"), "Category")
        self.assertEqual(label("depop_specifics.parcelSize"), "Parcel size")
        self.assertEqual(label("ebay_specifics.category_specifics.Sleeve Length"), "Sleeve Length")
        self.assertEqual(label("poshmark_specifics.originalPrice"), "Original price")


class PreflightReadinessTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        store = LocalPhotoStore(directory)
        store.put("front.jpg", b"photo")
        self.enterContext(patch("vendoo_studio.services.photo_store._store", store))
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

    def save(self, **changes):
        listing_persistence.save_revision(self.db, self.conv.id, {**copy.deepcopy(LISTING), **changes}, "user_form")

    def test_required_form_fields_left_empty_count_before_anything_is_listed(self):
        result = native.preflight(self.conv.id, self.db)
        self.assertIn("Listing Price", labels(result, "poshmark"))
        self.assertIn("Item price", labels(result, "depop"))
        self.assertIn("Package size", labels(result, "depop"))
        # Saying so does not refuse List on its own: the marketplace does.
        self.assertIn("Item price", labels(result, "mercari"))
        self.assertTrue(result["marketplaces"]["mercari"]["ready"])
        # A row List fills from General is not missing: Condition follows General's.
        self.assertNotIn("Condition", labels(result, "poshmark"))

    def test_filling_a_field_takes_it_out_of_the_count(self):
        before = native.preflight(self.conv.id, self.db)
        self.save(price=30, depop_specifics={"parcelSize": "Medium"})
        after = native.preflight(self.conv.id, self.db)
        self.assertNotIn("Listing Price", labels(after, "poshmark"))
        self.assertEqual(len(labels(after, "poshmark")), len(labels(before, "poshmark")) - 1)
        self.assertEqual([name for name in labels(after, "depop") if name in {"Item price", "Package size"}], [])

    def test_a_rejected_field_counts_until_the_seller_edits_it(self):
        MarketplaceListingRepo(self.db).save(self.conv.id, "poshmark", {
            "state": "not_saved", "error": "Poshmark rejected the listing.",
            "error_fields": [{"field": "poshmark_specifics.originalPrice", "message": "Enter an original price."},
                             {"field": "price", "message": "Enter a price."}]})
        result = native.preflight(self.conv.id, self.db)
        self.assertIn("Original price", labels(result, "poshmark"))
        # Rejected and still empty is one field, not two.
        self.assertEqual(labels(result, "poshmark").count("Listing Price"), 1)
        self.assertEqual(len(labels(result, "poshmark")), len(set(labels(result, "poshmark"))))

    def test_the_inventory_reads_the_same_count_for_several_listings(self):
        other = ConversationRepo(self.db).create(title="Empty")
        response = self.client.get("/api/listing-readiness", params={"ids": [self.conv.id, other.id, "missing"]})
        self.assertEqual(response.status_code, 200, response.text)
        listings = response.json()["listings"]
        self.assertEqual(set(listings), {self.conv.id, other.id})
        editor = native.preflight(self.conv.id, self.db)
        for marketplace, check in editor["marketplaces"].items():
            self.assertEqual(listings[self.conv.id]["marketplaces"][marketplace]["missing"], check["missing"])
        self.assertEqual(listings[other.id], {"revision_id": None, "marketplaces": {}})


def test_hosted_readiness_reads_only_the_sellers_own_listings(cloud, signing_keys):  # noqa: F811
    client, _ = cloud
    alice, bob = headers(signing_keys), headers(signing_keys, BOB)
    draft = client.post('/api/drafts', headers=alice, json={'title': 'Vintage coat'}).json()
    saved = client.patch(f"/api/drafts/{draft['id']}", headers=alice, json={
        'title': 'Vintage coat', 'notes': '', 'expected_updated_at': draft['updated_at'],
        'fields': {'price': 40, 'brand': 'Pendleton', 'description': 'Warm wool coat.'},
    })
    assert saved.status_code == 200, saved.text
    mine = client.get('/api/listing-readiness', headers=alice, params={'ids': [draft['id']]})
    assert mine.status_code == 200, mine.text
    assert set(mine.json()['listings']) == {draft['id']}
    assert any(mine.json()['listings'][draft['id']]['marketplaces']['poshmark']['missing'])
    theirs = client.get('/api/listing-readiness', headers=bob, params={'ids': [draft['id']]})
    assert theirs.status_code == 200
    assert theirs.json()['listings'] == {}
