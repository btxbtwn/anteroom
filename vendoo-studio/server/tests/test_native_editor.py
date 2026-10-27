"""The direct editor reads catalogs without starting jobs or touching live listings."""
from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, get_db, load_models
from vendoo_studio.main import app
from vendoo_studio.repositories.queries import ConversationRepo, JobRepo, ListingRepo
from vendoo_studio.services import native_editor, marketplace_schemas, ebay_listings
from vendoo_studio.services.listing_result import ListingProblem
from vendoo_studio.services.vendoo_import import merge_notes


class NativeEditorTest(unittest.TestCase):
    def setUp(self):
        load_models()
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.conv = ConversationRepo(self.db).create(title="Private native form test")
        self.listing = {"title": "Nike L Tee", "description": "Details", "brand": "Nike", "size": "L", "price": 20,
                        "condition": "Pre-Owned - Good", "direct_category_ids": {"ebay": "15687", "poshmark": "men/shirts"}}
        self.rev = ListingRepo(self.db).save_revision(self.conv.id, self.listing, "test")
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)
        self.url = f"/api/conversations/{self.conv.id}/native-marketplaces"
        self.enterContext(patch("vendoo_studio.routes.extension.extension_manager", SimpleNamespace(connected=True)))
        self.bridge = self.enterContext(patch("vendoo_studio.services.native_editor.browser_bridge.request", new_callable=AsyncMock))
        self.bridge.return_value = {"ok": True, "form": {"marketplace": "poshmark", "native": True, "known": True, "fields": []}}

    def tearDown(self):
        app.dependency_overrides.pop(get_db, None)
        self.db.close()
        self.engine.dispose()

    def test_form_read_uses_posting_snapshot_and_does_not_save_or_launch(self):
        self.conv.notes = merge_notes(self.conv.notes, {"cog": "3.65", "packageDimensions": "13x10x3", "sku": "SKU1"})
        self.db.commit()
        before = copy.deepcopy(self.rev.listing_json)
        answer = self.client.get(f"{self.url}/poshmark/form?revision_id={self.rev.id}")
        self.assertEqual(answer.status_code, 200, answer.text)
        payload = self.bridge.call_args.args[2]
        self.assertEqual(self.bridge.call_args.args[1], "marketplace.form")
        self.assertEqual(payload["listing"]["cost"], 3.65)
        self.assertEqual(payload["listing"]["package_dimensions_in"], "13x10x3")
        self.assertEqual(payload["listing"]["sku"], "SKU1")
        self.assertNotIn("confirm", payload)
        self.assertIsNone(self.bridge.call_args.args[0].id)
        self.assertEqual(ListingRepo(self.db).get_current(self.conv.id).current_revision_id, self.rev.id)
        self.assertEqual(ListingRepo(self.db).get_revision(self.rev.id).listing_json, before)
        self.assertEqual(JobRepo(self.db).list_by_conversation(self.conv.id), [])

    def test_stale_revision_and_disconnected_chrome_do_not_fall_back_to_vendoo(self):
        stale = self.client.get(f"{self.url}/poshmark/form?revision_id=old")
        self.assertEqual(stale.status_code, 409)
        self.bridge.assert_not_called()
        with patch("vendoo_studio.routes.extension.extension_manager", SimpleNamespace(connected=False)):
            answer = self.client.get(f"{self.url}/depop/form")
        self.assertEqual(answer.status_code, 409)
        self.assertIn("Connect Chrome", answer.json()["detail"])
        self.bridge.assert_not_called()

    def test_catalog_refusal_is_visible_and_never_returns_a_different_form(self):
        self.bridge.return_value = {"ok": False, "error": "Depop’s category list changed."}
        answer = self.client.get(f"{self.url}/depop/form")
        self.assertEqual(answer.status_code, 502)
        self.assertEqual(answer.json()["detail"], "Depop’s category list changed.")
        self.bridge.return_value = {"ok": True, "form": {"marketplace": "poshmark", "native": True}}
        self.assertEqual(self.client.get(f"{self.url}/depop/form").status_code, 502)

    def test_native_choices_survive_form_save_and_posting_preparation(self):
        submitted = {**self.listing, "poshmark_specifics": {"sizeSetId": "native-group", "nativeSizeId": "XL", "smartSell": {"enabled": False}},
                     "depop_specifics": {"nativeSizeId": "123", "attributes": {"bottom-fit": ["straight"]}},
                     "mercari_specifics": {"nativeSizeId": "168"}}
        answer = self.client.put(f"/api/conversations/{self.conv.id}/listing", json={"listing": submitted})
        self.assertEqual(answer.status_code, 200, answer.text)
        self.client.get(f"{self.url}/poshmark/form")
        snapshot = self.bridge.call_args.args[2]["listing"]
        self.assertEqual(snapshot["poshmark_specifics"]["nativeSizeId"], "XL")
        self.assertEqual(snapshot["poshmark_specifics"]["sizeSetId"], "native-group")
        self.assertIs(snapshot["poshmark_specifics"]["smartSell"]["enabled"], False)
        self.assertEqual(snapshot["depop_specifics"]["attributes"], {"bottom-fit": ["straight"]})
        self.assertEqual(snapshot["depop_specifics"]["nativeSizeId"], "123")
        self.assertEqual(snapshot["mercari_specifics"]["nativeSizeId"], "168")

    def test_sellwild_form_is_native_and_has_no_ignored_brand_or_quantity(self):
        answer = self.client.get(f"{self.url}/sellwild/form")
        self.assertEqual(answer.status_code, 200, answer.text)
        form = answer.json()
        self.assertTrue(form["native"])
        fields = {row["path"]: row for row in form["fields"]}
        self.assertNotIn("brand", fields)
        self.assertNotIn("quantity", fields)
        self.assertNotIn("locked", fields["sellwild_specifics.nativeCategoryId"])
        self.assertTrue(fields["sellwild_specifics.nativeCategoryId"]["selection_only"])
        self.assertIn("246", fields["sellwild_specifics.nativeCategoryId"]["option_values"].values())
        self.assertFalse(fields["sellwild_specifics.description"]["required"])
        self.assertEqual(fields["sellwild_specifics.title"]["value"], "Nike L Tee")
        self.assertEqual(fields["sellwild_specifics.description"]["value"], "Details")
        self.bridge.assert_not_called()

    def test_sellwild_form_follows_the_sell_page_sections_and_order(self):
        answer = self.client.get(f"{self.url}/sellwild/form")
        self.assertEqual(answer.status_code, 200, answer.text)
        rows = answer.json()["fields"]
        self.assertEqual([(row.get("section"), row["label"]) for row in rows], [
            ("Add a Title", "What are you selling?"), ("Name Your Price (USD)", "Price"),
            ("Add Details", "Describe your item (Optional)"), ("Add Details", "Category"), ("Add Details", "Condition"),
            ("Item Shipping", "Package"), ("Item Shipping", "Weight (lbs)"), ("Item Shipping", "Height (in)"),
            ("Item Shipping", "Width (in)"), ("Item Shipping", "Depth (in)"),
        ])
        fields = {row["label"]: row for row in rows}
        self.assertEqual(fields["Price"]["min_value"], 5)
        category = fields["Category"]
        # The picker's order: groups and their categories by Sellwild's sort order. Pets > Adoption is hidden.
        self.assertEqual(category["options"][:3], ["Vehicles | Cars", "Vehicles | Electric Cars", "Vehicles | SUVs"])
        self.assertNotIn("206", category["option_values"].values())
        self.assertEqual(fields["Package"]["options"][:6], [
            "Custom Package Dimensions", "Small Flat Rate Box", "Medium Flat Rate Box 1", "Medium Flat Rate Box 2",
            "Large Flat Rate Box", "Flat Rate Envelope (12.5 × 9.5 × 0.75 in)"])
        self.assertEqual(fields["Package"]["options"][-2:], ["Soft Pack (poly mailer)", "Irregular Parcel (4 × 4 × 4 in)"])
        self.assertEqual({fields[label].get("width") for label in ("Weight (lbs)", "Height (in)", "Width (in)", "Depth (in)")},
                         {"half"})

    def test_sellwild_vehicle_form_has_vehicle_information_and_no_package(self):
        submitted = {**self.listing, "sellwild_specifics": {"nativeCategoryId": "43", "vin": "1HGCM82633A004352"}}
        self.assertEqual(self.client.put(f"/api/conversations/{self.conv.id}/listing", json={"listing": submitted}).status_code, 200)
        rows = self.client.get(f"{self.url}/sellwild/form").json()["fields"]
        self.assertEqual([(row.get("section"), row["label"]) for row in rows][-2:], [
            ("Vehicle Information", "Vehicle Identification Number"), ("Vehicle Information", "Mileage")])
        self.assertNotIn("Package", [row["label"] for row in rows])

    def test_direct_depop_attribute_edits_survive_generated_style_padding(self):
        self.bridge.return_value = {"ok": True, "form": {"marketplace": "depop", "native": True, "fields": []}}
        submitted = {**self.listing, "depop_specifics": {
            "style": ["cute"], "age": [], "source": [],
        }}
        answer = self.client.put(f"/api/conversations/{self.conv.id}/listing", json={"listing": submitted})
        self.assertEqual(answer.status_code, 200, answer.text)
        response = self.client.get(f"{self.url}/depop/form")
        self.assertEqual(response.status_code, 200, response.text)
        snapshot = self.bridge.call_args.args[2]["listing"]
        self.assertEqual(snapshot["depop_specifics"]["style"], ["cute"])
        self.assertEqual(snapshot["depop_specifics"]["age"], [])
        self.assertEqual(snapshot["depop_specifics"]["source"], [])

    def test_seller_original_price_survives_snapshot_defaults(self):
        submitted = {**self.listing, "poshmark_specifics": {"originalPrice": 55}}
        response = self.client.put(f"/api/conversations/{self.conv.id}/listing", json={"listing": submitted})
        self.assertEqual(response.status_code, 200, response.text)
        self.client.get(f"{self.url}/poshmark/form")
        self.assertEqual(self.bridge.call_args.args[2]["listing"]["poshmark_specifics"]["originalPrice"], 55)

    def test_clearing_existing_depop_age_and_source_persists(self):
        previous = {**self.listing, "depop_specifics": {"style": ["Casual", "Retro", "Minimalist"], "age": "Modern", "source": "Preloved"}}
        ListingRepo(self.db).save_revision(self.conv.id, previous, "test")
        submitted = {**previous, "quantity": 3, "depop_specifics": {"style": ["casual", "retro"], "age": [], "source": []}}
        response = self.client.put(f"/api/conversations/{self.conv.id}/listing", json={"listing": submitted})
        self.assertEqual(response.status_code, 200, response.text)
        revision = ListingRepo(self.db).get_revision(ListingRepo(self.db).get_current(self.conv.id).current_revision_id)
        self.assertEqual(revision.listing_json["depop_specifics"]["age"], [])
        saved = self.client.get(f"/api/conversations/{self.conv.id}/listing").json()["listing"]
        self.assertEqual(saved["depop_specifics"]["age"], [])
        self.assertEqual(saved["depop_specifics"]["source"], [])
        self.assertEqual(saved["quantity"], 3)


class NativeEbayFieldsTest(unittest.IsolatedAsyncioTestCase):
    async def test_category_search_keeps_the_breadcrumb_in_root_to_leaf_order(self):
        from vendoo_studio.services.ebay_workspace import read

        client = SimpleNamespace(request=AsyncMock(return_value={"categorySuggestions": [{
            "category": {"categoryId": "15709", "categoryName": "Athletic Shoes"},
            "categoryTreeNodeAncestors": [{"categoryName": "Men’s Shoes"}, {"categoryName": "Men"}, {"categoryName": "Clothing"}],
        }]}))
        result = await read(client, "categories", query="mens sneakers")
        self.assertEqual(result["rows"][0]["subtitle"], "Clothing › Men › Men’s Shoes")
        self.assertEqual(result["rows"][0]["id"], "15709")

    async def test_ebay_form_keeps_native_ids_limits_and_multiselects(self):
        rows = [{"id": "condition", "name": "Condition", "required": True, "multi": False, "mode": "selection",
                 "values": [{"id": "1000", "label": "New"}, {"id": "3000", "label": "Pre-owned"}]},
                {"id": "Season", "name": "Season", "required": False, "multi": True, "mode": "selection", "max_length": 65,
                 "usage": "RECOMMENDED", "values": [{"id": "Spring", "label": "Spring"}, {"id": "Summer", "label": "Summer"}]},
                {"id": "Theme", "name": "Theme", "required": False, "multi": False, "mode": "free_text", "usage": "OPTIONAL",
                 "values": []}]
        listing = {"direct_category_ids": {"ebay": "15687"}, "condition": "Good", "ebay_specifics": {"season": ["Spring", "Summer"]}}
        with patch.object(marketplace_schemas, "fields", new=AsyncMock(return_value=rows)):
            form = await native_editor.ebay_form(listing)
        fields = {row["path"]: row for row in form["fields"]}
        self.assertEqual(fields["ebay_specifics.Condition"]["value"], "3000")
        self.assertEqual(fields["ebay_specifics.Condition"]["option_values"]["New"], "1000")
        self.assertEqual(fields["ebay_specifics.Season"]["value"], ["Spring", "Summer"])
        self.assertTrue(fields["ebay_specifics.Season"]["multi"])
        self.assertEqual(fields["ebay_specifics.Season"]["max_length"], 65)
        self.assertTrue(fields["ebay_specifics.Season"]["category_specific"])
        self.assertNotIn("on_form", fields["ebay_specifics.Season"])
        self.assertNotIn("on_form", fields["ebay_specifics.Theme"])
        self.assertTrue(fields["ebay_specifics.Theme"]["category_specific"])
        self.assertNotIn("category_specific", fields["ebay_specifics.Condition"])
        self.assertEqual(fields["ebay_specifics.bestOffer.enabled"]["input_type"], "boolean")
        self.assertNotIn("locked", fields["ebay_specifics.conditionDescription"])
        self.assertEqual(fields["ebay_specifics.conditionDescription"]["max_length"], 1000)
        self.assertEqual(ebay_listings.ebay_condition({"ebay_specifics": {"condition": "1000"}}), "NEW")
        self.assertEqual(fields["sku"]["value"], "")

    async def test_ebay_format_choices_come_from_ebays_category_rules(self):
        from vendoo_studio.services.ebay_client import EbayClient

        rows = [{"id": "condition", "name": "Condition", "required": True, "multi": False, "mode": "selection", "values": []}]
        auction = {"FIXED_PRICE": ["GTC"], "AUCTION": ["DAYS_3", "DAYS_7"]}
        listing = {"direct_category_ids": {"ebay": "15687"}, "condition": "Good",
                   "ebay_specifics": {"listingFormat": "AUCTION", "auctionStartPrice": "9.99"}}
        with patch.object(marketplace_schemas, "fields", new=AsyncMock(return_value=rows)), \
                patch.object(EbayClient, "connect", new=AsyncMock()), \
                patch.object(marketplace_schemas, "ebay_listing_types", new=AsyncMock(return_value=auction)):
            form = await native_editor.ebay_form(listing)
            fixed = await native_editor.ebay_form({**listing, "ebay_specifics": {}})
        fields = {row["path"]: row for row in form["fields"]}
        self.assertEqual(fields["ebay_specifics.listingFormat"]["option_values"], {"Fixed price": "FIXED_PRICE", "Auction": "AUCTION"})
        self.assertEqual(fields["ebay_specifics.listingFormat"]["value"], "AUCTION")
        self.assertIn("ebay_specifics.auctionStartPrice", fields["ebay_specifics.listingFormat"]["clears"])
        self.assertEqual(fields["ebay_specifics.listingDuration"]["option_values"],
                         {"Good 'Til Cancelled": "GTC", "3 days": "DAYS_3", "7 days": "DAYS_7"})
        self.assertFalse(fields["ebay_specifics.listingDuration"].get("locked"))
        self.assertEqual(fields["ebay_specifics.listingDuration"]["value"], "DAYS_7")
        self.assertEqual(fields["ebay_specifics.auctionStartPrice"]["value"], "9.99")
        self.assertIn("ebay_specifics.auctionBuyItNow", fields)
        self.assertIn("ebay_specifics.bestOffer.enabled", fields)
        fixed_fields = {row["path"]: row for row in fixed["fields"]}
        self.assertEqual(set(fields), set(fixed_fields))
        self.assertEqual(fixed_fields["ebay_specifics.listingFormat"]["value"], "FIXED_PRICE")
        self.assertEqual(fixed_fields["ebay_specifics.listingDuration"]["value"], "GTC")
        self.assertIn("ebay_specifics.auctionStartPrice", fixed_fields)
        self.assertIn("ebay_specifics.bestOffer.enabled", fixed_fields)
        # No auction in the category: Auction is not offered.
        with patch.object(marketplace_schemas, "fields", new=AsyncMock(return_value=rows)), \
                patch.object(EbayClient, "connect", new=AsyncMock()), \
                patch.object(marketplace_schemas, "ebay_listing_types", new=AsyncMock(return_value={"FIXED_PRICE": ["GTC"]})):
            plain = await native_editor.ebay_form({**listing, "ebay_specifics": {}})
        choice = next(row for row in plain["fields"] if row["path"] == "ebay_specifics.listingFormat")
        self.assertEqual(choice["options"], ["Fixed price"])
        self.assertIn("does not offer auctions", choice["help"])

    async def test_a_generated_ebay_inventory_key_is_not_shown_as_the_sku(self):
        rows = [{"id": "condition", "name": "Condition", "required": True, "multi": False, "mode": "selection", "values": []}]
        with patch.object(marketplace_schemas, "fields", new=AsyncMock(return_value=rows)):
            generated = await native_editor.ebay_form({"direct_category_ids": {"ebay": "15687"}, "sku": "ar-abc123", "condition": "Good"})
            typed = await native_editor.ebay_form({"direct_category_ids": {"ebay": "15687"}, "sku": "HAT-1", "condition": "Good"})
        generated_sku = next(row for row in generated["fields"] if row["path"] == "sku")
        typed_sku = next(row for row in typed["fields"] if row["path"] == "sku")
        self.assertEqual(generated_sku["value"], "")
        self.assertEqual(typed_sku["value"], "HAT-1")

    async def test_ebay_size_and_department_are_editable_and_filled_from_general(self):
        rows = [
            {"id": "Size", "name": "Size", "required": True, "multi": False, "mode": "selection",
             "values": [{"id": "M", "label": "M"}, {"id": "L", "label": "L"}]},
            {"id": "Department", "name": "Department", "required": False, "multi": False, "mode": "free",
             "values": []},
        ]
        listing = {
            "direct_category_ids": {"ebay": "15687"},
            "title": "General title",
            "description": "General description",
            "condition": "Good",
            "ebay_specifics": {"title": "eBay title"},
        }
        with patch.object(marketplace_schemas, "fields", new=AsyncMock(return_value=rows)):
            form = await native_editor.ebay_form(listing)
        fields = {row["path"]: row for row in form["fields"]}
        for path in ("ebay_specifics.Size", "ebay_specifics.Department"):
            row = fields[path]
            self.assertFalse(row.get("locked"))
            self.assertIn("stays on eBay", row["help"])
            self.assertNotIn("Change it there", row["help"])
        self.assertEqual(fields["ebay_specifics.title"]["value"], "eBay title")
        self.assertEqual(fields["ebay_specifics.description"]["value"], "General description")
        self.assertFalse(fields["ebay_specifics.title"].get("locked"))

    def test_ebay_size_follows_size_type_from_taxonomys_parent_constraints(self):
        size = {
            "name": "Size", "required": True, "multi": False, "mode": "selection", "max_length": None,
            "values": [
                {"id": "M", "label": "M", "constraints": [{
                    "applicableForLocalizedAspectName": "Size Type",
                    "applicableForLocalizedAspectValues": ["Regular"],
                }]},
                {"id": "M", "label": "M", "constraints": [{
                    "applicableForLocalizedAspectName": "Size Type",
                    "applicableForLocalizedAspectValues": ["Petites"],
                }]},
                {"id": "S", "label": "S", "constraints": [{
                    "applicableForLocalizedAspectName": "Size Type",
                    "applicableForLocalizedAspectValues": ["Regular"],
                }]},
            ],
        }
        size_type = {"name": "Size Type", "required": True, "multi": False, "mode": "selection", "max_length": None,
                     "values": [{"id": "Regular", "label": "Regular"}, {"id": "Petites", "label": "Petites"}]}
        stored = marketplace_schemas._ebay_field({
            "localizedAspectName": "Size", "aspectConstraint": {"aspectMode": "SELECTION_ONLY", "aspectRequired": True},
            "aspectValues": [{"localizedValue": "M", "valueConstraints": size["values"][0]["constraints"]},
                             {"localizedValue": "M", "valueConstraints": size["values"][1]["constraints"]}]})
        self.assertEqual(stored["values"][0]["constraints"], [{"parentName": "Size Type", "parentValues": ["Regular"]}])
        self.assertEqual(marketplace_schemas.constraints_by_label(stored["values"])["M"],
                         [{"parentName": "Size Type", "parentValues": ["Regular", "Petites"]}])
        matched = ebay_listings._match_aspects([size_type, size], {"Size": ["M"], "Size Type": ["Regular"]})
        self.assertEqual(matched["Size"], ["M"])
        self.assertEqual(matched["Size Type"], ["Regular"])
        listing = {"size": "S", "sizeType": "Petites", "ebay_specifics": {"size": "M", "sizeType": "Regular"}}
        aspects = ebay_listings.ebay_aspects(listing, {})
        self.assertEqual(aspects["Size"], ["M"])
        self.assertEqual(aspects["Size Type"], ["Regular"])
        with self.assertRaises(ListingProblem) as caught:
            ebay_listings._match_aspects([size_type, size], {"Size": ["S"], "Size Type": ["Petites"]})
        self.assertIn("Choose Size for the selected parent aspects", str(caught.exception))

    def test_ebay_parent_constraints_are_preserved_and_enforced_before_posting(self):
        field = marketplace_schemas._ebay_field({"localizedAspectName": "Model", "aspectConstraint": {"aspectMode": "SELECTION_ONLY"},
            "aspectValues": [{"localizedValue": "Air Max", "valueConstraints": [{"parentName": "Brand", "parentValues": ["Nike"]}]},
                             {"localizedValue": "Gazelle", "valueConstraints": [{"parentName": "Brand", "parentValues": ["Adidas"]}]}]})
        self.assertEqual([v["label"] for v in marketplace_schemas.aspect_options(field, {"Brand": ["Nike"]})], ["Air Max"])
        with self.assertRaises(ListingProblem) as caught:
            ebay_listings._match_aspects([field], {"Brand": ["Adidas"], "Model": ["Air Max"]})
        self.assertIn("selected parent aspects", str(caught.exception))

    def test_an_explicitly_cleared_ebay_aspect_does_not_reappear_from_general_values(self):
        listing = {"brand": "Nike", "material": "Cotton", "ebay_specifics": {"brand": "", "material": []}}
        self.assertNotIn("Brand", ebay_listings.ebay_aspects(listing, {"overrides": {"brand": "Nike"}}))
        self.assertNotIn("Material", ebay_listings.ebay_aspects(listing, {}))
