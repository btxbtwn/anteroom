"""Bottoms sizes for every marketplace, and eBay Best Offer floors."""

from __future__ import annotations

import copy
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, get_db
from vendoo_studio.main import app
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.fill_log import FillLogEntry  # noqa: F401
from vendoo_studio.models.job import Job  # noqa: F401
from vendoo_studio.models.registry import FieldRegistry  # noqa: F401
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services.bottoms_sizes import apply_bottoms_sizes
from vendoo_studio.services.listing_generate import align_size_fields
from vendoo_studio.services.vendoo_marketplace_fields import best_offer_error


def _client(db):
    def override():
        yield db
    app.dependency_overrides[get_db] = override
    return TestClient(app)


JEANS = {
    "title": "Levi's 32x30 Vintage 501 Jeans Blue Straight",
    "description": "Vintage jeans.\n\nMeasurements: Waist: 16\"; Inseam: 30\"",
    "price": 40,
    "brand": "Levi's",
    "size": "32x30",
    "department": "Men",
    "condition": "Pre-Owned - Good",
    "category_path": "Clothing, Shoes & Accessories > Men > Men's Clothing > Jeans",
    "ebay_specifics": {"type": "Jeans", "size": "32x30"},
    "poshmark_specifics": {},
    "mercari_specifics": {},
    "depop_specifics": {"source": "Preloved", "age": "90s", "style": ["Casual"], "parcelSize": "Medium"},
    "etsy_specifics": {"who_made": "Another company or person", "what_is": "A finished product", "when_made": "1990s"},
    "vinted_specifics": {},
    "sellwild_specifics": {},
}


class BottomsSizeTest(unittest.TestCase):
    def test_a_mens_waist_and_inseam_lands_on_every_marketplace(self):
        listing = copy.deepcopy(JEANS)
        self.assertTrue(align_size_fields(listing))
        self.assertEqual(listing["size"], "32")
        self.assertEqual(listing["title"], "Levi's 32 Vintage 501 Jeans Blue Straight")
        self.assertEqual(listing["ebay_specifics"]["size"], "32")
        self.assertEqual(listing["ebay_specifics"]["inseam"], "30")
        self.assertEqual(listing["poshmark_specifics"]["size"], "Waist 32")
        self.assertEqual(listing["mercari_specifics"]["size"], "32")
        self.assertEqual(listing["depop_specifics"]["size"], "32")
        self.assertEqual(listing["etsy_specifics"]["size"], "32")
        self.assertEqual(listing["etsy_specifics"]["inseam"], "30")
        self.assertEqual(listing["vinted_specifics"]["size"], "32")
        # Sellwild's sell form has no size field.
        self.assertEqual(listing["sellwild_specifics"], {})
        # A second pass keeps the marketplace spelling. It does not collapse Waist 32.
        again = copy.deepcopy(listing)
        align_size_fields(again)
        self.assertEqual(again["poshmark_specifics"]["size"], "Waist 32")
        self.assertEqual(again["ebay_specifics"]["inseam"], "30")

    def test_a_repeated_waist_does_not_invent_an_inseam(self):
        listing = {
            "title": "Levi's 34x34 Y2K 501 Straight Jeans Blue Denim",
            "brand": "Levi's", "size": "34x34", "department": "Men",
            "description": "Y2K straight jeans.",
            "ebay_specifics": {"type": "Jeans", "size": "34x34"},
        }
        align_size_fields(listing)
        self.assertEqual(listing["size"], "34")
        self.assertNotIn("inseam", listing["ebay_specifics"])

    def test_womens_numeric_and_petite_map_without_a_mens_waist(self):
        listing = {
            "title": "Anne Klein 8P Ankle Pants Black Slim",
            "brand": "Anne Klein", "size": "8P", "department": "Women",
            "description": "Ankle pants.",
            "category_path": "Clothing > Women > Pants",
            "ebay_specifics": {"type": "Pants"},
            "depop_specifics": {"source": "Preloved", "age": "Modern", "style": ["Casual"], "parcelSize": "Small"},
        }
        apply_bottoms_sizes(listing)
        self.assertEqual(listing["ebay_specifics"]["size"], "8")
        self.assertEqual(listing["ebay_specifics"]["sizeType"], "Petites")
        self.assertEqual(listing["poshmark_specifics"]["size"], "8P")
        self.assertEqual(listing["poshmark_specifics"]["sizeType"], "Petite")
        self.assertEqual(listing["mercari_specifics"]["size"], "8")
        self.assertEqual(listing["depop_specifics"]["size"], "8")
        self.assertEqual(listing["depop_specifics"]["sizeGrouping"], "Petite")
        self.assertEqual(listing["etsy_specifics"]["size"], "8")
        self.assertEqual(listing["vinted_specifics"], {"size": "S / 8"})
        # Neither Sellwild's nor Vinted's form has a size type field.
        self.assertNotIn("sellwild_specifics", listing)

    def test_a_seller_size_is_not_replaced(self):
        listing = copy.deepcopy(JEANS)
        listing["size"] = "32"
        listing["ebay_specifics"]["size"] = "32"
        listing["ebay_specifics"]["inseam"] = "30"
        listing["poshmark_specifics"] = {"size": "Waist 40"}
        listing["seller_fields"] = ["poshmark_specifics.size"]
        apply_bottoms_sizes(listing)
        self.assertEqual(listing["poshmark_specifics"]["size"], "Waist 40")

    def test_best_offer_order_is_minimum_then_auto_accept_then_price(self):
        listing = {"price": 17, "ebay_specifics": {"bestOffer": {"autoAcceptPrice": 15, "minimumOfferPrice": 12}}}
        self.assertIsNone(best_offer_error(listing))
        listing["ebay_specifics"]["bestOffer"]["minimumOfferPrice"] = 16
        self.assertIn("minimum", best_offer_error(listing) or "")
        listing["ebay_specifics"]["bestOffer"] = {"enabled": "No", "minimumOfferPrice": 16, "autoAcceptPrice": 12}
        self.assertIsNone(best_offer_error(listing))


class BottomsAndOfferRouteTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.conv = Conversation(title="Jeans")
        self.db.add(self.conv)
        self.db.commit()
        self.client = _client(self.db)
        ListingRepo(self.db).save_revision(self.conv.id, copy.deepcopy(JEANS), source="model")

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def test_cli_fields_set_a_bottoms_size_and_it_sticks(self):
        url = f"/api/conversations/{self.conv.id}/fields"
        response = self.client.post(url, json={"path": "poshmark_specifics.size", "value": "Waist 38"})
        self.assertEqual(response.status_code, 200, response.text)
        listing = self.client.get(f"/api/conversations/{self.conv.id}/listing").json()["listing"]
        self.assertEqual(listing["poshmark_specifics"]["size"], "Waist 38")
        self.assertIn("poshmark_specifics.size", listing["seller_fields"])
        self.assertEqual(listing["ebay_specifics"]["inseam"], "30")
        saved = self.client.put(
            f"/api/conversations/{self.conv.id}/listing",
            json={"expected_revision_id": ConversationRepo(self.db).current_revision_id(self.conv.id) or "", "listing": {**listing, "title": listing["title"]}},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        again = self.client.get(f"/api/conversations/{self.conv.id}/listing").json()["listing"]
        self.assertEqual(again["poshmark_specifics"]["size"], "Waist 38")

    def test_cli_and_chat_reject_a_best_offer_minimum_above_the_auto_accept(self):
        listing = copy.deepcopy(JEANS)
        listing["price"] = 17
        listing["ebay_specifics"]["bestOffer"] = {"enabled": "Yes", "autoAcceptPrice": 12, "minimumOfferPrice": 15}
        response = self.client.put(f"/api/conversations/{self.conv.id}/listing", json={"expected_revision_id": ConversationRepo(self.db).current_revision_id(self.conv.id) or "", "listing": listing})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("minimum", response.json()["detail"])
        kept = self.client.get(f"/api/conversations/{self.conv.id}/listing").json()["listing"]
        self.assertNotIn("bestOffer", kept.get("ebay_specifics") or {})

        valid = copy.deepcopy(JEANS)
        valid["price"] = 17
        valid["ebay_specifics"]["bestOffer"] = {"enabled": "Yes", "autoAcceptPrice": 15, "minimumOfferPrice": 12}
        saved = self.client.put(f"/api/conversations/{self.conv.id}/listing", json={"expected_revision_id": ConversationRepo(self.db).current_revision_id(self.conv.id) or "", "listing": valid})
        self.assertEqual(saved.status_code, 200, saved.text)
        bad = self.client.post(
            f"/api/conversations/{self.conv.id}/fields",
            json={"path": "ebay_specifics.bestOffer.minimumOfferPrice", "value": 16},
        )
        self.assertEqual(bad.status_code, 400, bad.text)
        stored = self.client.get(f"/api/conversations/{self.conv.id}/listing").json()["listing"]
        self.assertEqual(stored["ebay_specifics"]["bestOffer"]["autoAcceptPrice"], 15)
        self.assertEqual(stored["ebay_specifics"]["bestOffer"]["minimumOfferPrice"], 12)

    def test_chat_json_sets_a_size_and_refuses_a_bad_offer(self):
        from vendoo_studio.services.chat_listing import apply_listing_payload

        _ops, saved = apply_listing_payload(
            self.db, self.conv.id,
            'Set the Mercari size.\n```json\n{"fields":[{"path":"mercari_specifics.size","value":"34"}]}\n```',
        )
        self.assertTrue(saved)
        current = ListingRepo(self.db).get_current(self.conv.id)
        listing = ListingRepo(self.db).get_revision(current.current_revision_id).listing_json
        self.assertEqual(listing["mercari_specifics"]["size"], "34")
        _ops, saved = apply_listing_payload(
            self.db, self.conv.id,
            '```json\n{"fields":[{"path":"ebay_specifics.bestOffer.autoAcceptPrice","value":12},'
            '{"path":"ebay_specifics.bestOffer.minimumOfferPrice","value":15}]}\n```',
        )
        self.assertFalse(saved)
        from vendoo_studio.repositories.queries import ConversationRepo

        notes = [message.text for message in ConversationRepo(self.db).get_messages(self.conv.id)]
        self.assertTrue(any("minimum offer" in note for note in notes), notes)
        kept = ListingRepo(self.db).get_revision(
            ListingRepo(self.db).get_current(self.conv.id).current_revision_id
        ).listing_json
        self.assertEqual(kept["mercari_specifics"]["size"], "34")
        self.assertNotIn("bestOffer", kept.get("ebay_specifics") or {})
