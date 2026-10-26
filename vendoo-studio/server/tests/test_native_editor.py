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
        self.assertTrue(fields["sellwild_specifics.nativeCategoryId"]["locked"])
        self.bridge.assert_not_called()

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
                 "values": [{"id": "Spring", "label": "Spring"}, {"id": "Summer", "label": "Summer"}]}]
        listing = {"direct_category_ids": {"ebay": "15687"}, "condition": "Good", "ebay_specifics": {"season": ["Spring", "Summer"]}}
        with patch.object(marketplace_schemas, "fields", new=AsyncMock(return_value=rows)):
            form = await native_editor.ebay_form(listing)
        fields = {row["path"]: row for row in form["fields"]}
        self.assertEqual(fields["ebay_specifics.Condition"]["value"], "3000")
        self.assertEqual(fields["ebay_specifics.Condition"]["option_values"]["New"], "1000")
        self.assertEqual(fields["ebay_specifics.Season"]["value"], ["Spring", "Summer"])
        self.assertTrue(fields["ebay_specifics.Season"]["multi"])
        self.assertEqual(fields["ebay_specifics.Season"]["max_length"], 65)
        self.assertEqual(ebay_listings.ebay_condition({"ebay_specifics": {"condition": "1000"}}), "NEW")

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
