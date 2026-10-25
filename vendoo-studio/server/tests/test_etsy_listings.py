from __future__ import annotations

import copy
import io
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, get_db
from vendoo_studio.main import app
from vendoo_studio.repositories.queries import ConversationRepo, JobRepo, ListingRepo
from vendoo_studio.services import etsy_listings, relay_client

LISTING = {"title": "Private api check - do not publish", "description": "Private draft check.", "price": 12,
           "marketplace_category_ids": {"etsy": 482}, "etsy_specifics": {
               "who_made": "Another company or person", "what_is": "A finished product", "when_made": "1990s (Vintage)",
               "shipping_profile_id": "99", "readiness_state_id": "88"}}
URL = "https://www.etsy.com/your/shops/me/listing-editor/edit/123456"


class EtsyListingsTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)
        self.conv = ConversationRepo(self.db).create(title=LISTING["title"])
        self.rev = ListingRepo(self.db).save_revision(self.conv.id, copy.deepcopy(LISTING), "model")
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(etsy_listings, "PHOTOS_DIR", self.temp))
        for index in range(2):
            name = f"test-{index}.jpg"
            Path(self.temp, name).write_bytes(b"image")
            ConversationRepo(self.db).add_photo(self.conv.id, name, name, "image/jpeg", 5)
        self.enterContext(patch.object(etsy_listings, "get_marketplace_accounts", return_value={"etsy": {"accountId": "123", "accessToken": "never-expose"}}))
        self.enterContext(patch("vendoo_studio.routes.extension.extension_manager", SimpleNamespace(connected=False)))
        self.enterContext(patch("vendoo_studio.routes.extension.schedule_advance_job_queue"))
        self.bridge = self.enterContext(patch("vendoo_studio.services.native_marketplaces.browser_bridge.request", new_callable=AsyncMock))
        self.calls = []
        self.remote = None
        self.inventory = None
        self.failure = None
        self.calculated = False
        self.api = self.enterContext(patch.object(etsy_listings.marketplace_accounts, "etsy_api", side_effect=self.request))
        self.base = f"/api/conversations/{self.conv.id}/native-marketplaces/etsy"

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    async def request(self, method, path, **kwargs):
        data = copy.deepcopy(kwargs.get("data") or kwargs.get("json_body") or {})
        self.calls.append((method, path, data, bool(kwargs.get("files"))))
        if self.failure:
            result = self.failure(method, path)
            if result:
                raise result
        if path.endswith("/shipping-profiles"):
            return {"results": [{"shipping_profile_id": 99, "title": "Buyer pays", "origin_postal_code": "never-expose",
                                 "profile_type": "calculated" if self.calculated else "manual"}]}
        if path.endswith("/readiness-state-definitions"):
            return {"results": [{"readiness_state_id": 88, "readiness_state": "ready_to_ship", "processing_days_display_label": "1-2 days"}]}
        if method == "GET":
            return copy.deepcopy(self.inventory if path.endswith("/inventory") else self.remote)
        if method == "PUT":
            self.inventory = copy.deepcopy(data)
            for product in self.inventory["products"]:
                for offering in product["offerings"]:
                    offering["price"] = {"amount": int(Decimal(str(offering["price"])) * 100), "divisor": 100, "currency_code": "USD"}
            self.remote["price"] = self.inventory["products"][0]["offerings"][0]["price"]
            return copy.deepcopy(self.inventory)
        if path.endswith("/images"):
            image = {"listing_image_id": 1000 + len(self.remote["images"]), "rank": int(data["rank"])}
            self.remote["images"].append(image)
            return image
        if method == "POST":
            self.remote = {**data, "listing_id": 123456, "shop_id": 123, "state": "draft", "images": []}
        else:
            self.remote.update(data)
        if "price" in data:
            self.remote["price"] = {"amount": int(Decimal(data["price"]) * 100), "divisor": 100, "currency_code": "USD"}
        if method == "POST":
            self.inventory = {"products": [{"sku": "keep-sku", "property_values": [], "offerings": [{
                "price": copy.deepcopy(self.remote["price"]), "quantity": 1, "is_enabled": True, "readiness_state_id": 88}]}]}
        return copy.deepcopy(self.remote)

    def save(self):
        result = self.client.post(self.base + "/draft", json={"revision_id": self.rev.id})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def publish(self, **changes):
        return self.client.post(self.base + "/publish", json={"revision_id": self.rev.id, "draft_url": URL, "confirm": True, **changes})

    def test_non_vintage_when_made_is_a_year_range(self):
        made = {**LISTING, "etsy_specifics": {**LISTING["etsy_specifics"], "who_made": "I did", "when_made": "Made To Order (Not Yet Made)"}}
        self.assertEqual(etsy_listings.draft_fields(made, creating=True)["when_made"], "2010_2019")
        recent = {**LISTING, "etsy_specifics": {**LISTING["etsy_specifics"], "who_made": "I did", "when_made": "non-vintage"}}
        self.assertEqual(etsy_listings.draft_fields(recent, creating=True)["when_made"], "2010_2019")
        vintage = {**LISTING, "etsy_specifics": {**LISTING["etsy_specifics"], "when_made": "1990s (Vintage)"}}
        self.assertEqual(etsy_listings.draft_fields(vintage, creating=True)["when_made"], "1990s")

    def test_new_draft_uses_official_api_and_uploads_all_photos_without_chrome(self):
        saved = self.save()
        self.assertEqual(saved["state"], "draft")
        self.assertEqual(saved["transport"], "etsy_api")
        self.assertEqual(saved["draft_url"], URL)
        self.assertEqual(len(saved["etsy_image_ids"]), 2)
        created = next(c[2] for c in self.calls if c[0] == "POST" and c[1].endswith("/listings"))
        self.assertEqual(created["price"], "12.00")
        self.assertEqual(created["who_made"], "someone_else")
        self.assertEqual(created["when_made"], "1990s")
        self.assertEqual(created["should_auto_renew"], "false")
        self.assertTrue(all("state" not in c[2] for c in self.calls))
        self.assertEqual(sum(c[3] for c in self.calls), 2)
        self.assertNotIn("never-expose", str(saved))
        self.bridge.assert_not_called()
        self.assertEqual(JobRepo(self.db).list_all()[0].status, "completed")

    def test_update_keeps_native_photos_and_checks_new_price(self):
        self.save()
        self.calls.clear()
        self.rev = ListingRepo(self.db).save_revision(self.conv.id, {**LISTING, "price": 13}, "user")
        saved = self.save()
        self.assertEqual(saved["revision_id"], self.rev.id)
        self.assertEqual([c[0] for c in self.calls], ["GET", "GET", "PUT", "PATCH", "GET", "GET"])
        self.assertNotIn("price", next(c[2] for c in self.calls if c[0] == "PATCH"))
        inventory = next(c[2] for c in self.calls if c[0] == "PUT")
        self.assertEqual(inventory["products"][0]["sku"], "keep-sku")
        self.assertEqual(inventory["products"][0]["offerings"][0], {"price": 13.0, "quantity": 1, "is_enabled": True, "readiness_state_id": 88})
        self.assertEqual(self.remote["price"]["amount"], 1300)
        self.assertEqual(len(self.remote["images"]), 2)

    def test_publish_requires_confirmation_current_revision_and_same_draft(self):
        self.save()
        self.calls.clear()
        for changes, status in [({"confirm": False}, 400), ({"revision_id": "stale"}, 409), ({"draft_url": URL + "9"}, 409)]:
            self.assertEqual(self.publish(**changes).status_code, status)
        self.assertFalse(self.calls)

    def test_publish_is_one_confirmed_activation_and_cannot_run_twice(self):
        self.save()
        self.calls.clear()
        published = self.publish()
        self.assertEqual(published.json()["state"], "published", published.text)
        self.assertEqual(self.calls[-1], ("PATCH", "shops/123/listings/123456", {"state": "active"}, False))
        count = len(self.calls)
        self.assertEqual(self.publish().status_code, 409)
        self.assertEqual(len(self.calls), count)

    def test_update_changes_the_live_listing_and_does_not_create_another(self):
        self.save()
        self.assertEqual(self.publish().json()["state"], "published")
        self.calls.clear()
        self.rev = ListingRepo(self.db).save_revision(self.conv.id, {**LISTING, "title": "Updated tee", "price": 13}, "user")
        updated = self.client.post(self.base + "/update", json={"revision_id": self.rev.id, "confirm": True})
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["state"], "published")
        self.assertEqual(updated.json()["listing_url"], "https://www.etsy.com/listing/123456")
        self.assertNotIn(("POST", "shops/123/listings"), [(c[0], c[1]) for c in self.calls])
        self.assertFalse(any(c[2].get("state") for c in self.calls))
        self.assertEqual(self.remote["title"], "Updated tee")
        self.assertEqual(self.remote["state"], "active")
        self.assertEqual(self.publish().status_code, 409)

    def test_changed_remote_fields_or_shop_cannot_be_published(self):
        self.save()
        for changes in [{"title": "Changed"}, {"shop_id": 999}]:
            previous = copy.deepcopy(self.remote)
            self.remote.update(changes)
            self.calls.clear()
            self.assertNotEqual(self.publish().json()["state"], "published")
            self.assertTrue(all(c[0] == "GET" for c in self.calls))
            self.remote = previous

    def test_lost_publish_reply_is_read_only_on_check_and_never_replayed(self):
        self.save()
        def lost(method, path):
            if method == "PATCH":
                self.remote["state"] = "active"
                return httpx.ReadTimeout("lost response")
        self.failure = lost
        self.assertEqual(self.publish().json()["state"], "unknown")
        before = len(self.calls)
        self.assertEqual(self.publish().status_code, 409)
        self.assertEqual(len(self.calls), before)
        self.calls.clear()
        self.failure = None
        checked = self.client.post(self.base + "/check")
        self.assertEqual(checked.json()["state"], "published", checked.text)
        self.assertTrue(all(c[0] == "GET" for c in self.calls))

    def test_lost_create_reply_blocks_duplicate_creation(self):
        self.failure = lambda method, path: httpx.ReadTimeout("lost create") if method == "POST" else None
        self.assertEqual(self.save()["state"], "unknown")
        before = len(self.calls)
        r = self.client.post(self.base + "/draft", json={"revision_id": self.rev.id})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(len(self.calls), before)
        self.client.post(self.base + "/check")
        self.assertEqual(len(self.calls), before)

    def test_rejected_authorization_can_be_reconnected_without_an_unknown_create(self):
        self.failure = lambda method, path: etsy_listings.marketplace_accounts.SignInRequired("Reconnect Etsy") if method == "POST" else None
        self.assertEqual(self.save()["state"], "not_saved")
        self.failure = None
        self.assertEqual(self.save()["state"], "draft")

    def test_rejected_photo_resumes_same_draft_without_duplicate_images(self):
        def reject(method, path):
            if path.endswith("/images") and len(self.remote["images"]) == 1:
                response = httpx.Response(400, request=httpx.Request(method, "https://api.etsy.com/"))
                return httpx.HTTPStatusError("rejected", request=response.request, response=response)
        self.failure = reject
        saved = self.save()
        self.assertEqual(saved["state"], "draft")
        self.assertIsNone(saved["revision_id"])
        self.assertEqual(len(self.remote["images"]), 1)
        self.failure = None
        saved = self.save()
        self.assertEqual(saved["revision_id"], self.rev.id)
        self.assertEqual(len(self.remote["images"]), 2)
        self.assertEqual(sum(c[0] == "POST" and c[1] == "shops/123/listings" for c in self.calls), 1)

    def test_uncertain_activation_still_showing_a_draft_is_not_retryable(self):
        self.save()
        self.failure = lambda method, path: httpx.ReadTimeout("lost activation") if method == "PATCH" else None
        self.assertEqual(self.publish().json()["state"], "unknown")
        self.failure = None
        self.assertEqual(self.client.post(self.base + "/check").json()["state"], "unknown")
        self.assertEqual(self.publish().status_code, 409)

    def test_lost_last_photo_receipt_can_be_verified_without_reuploading(self):
        def lost(method, path):
            if path.endswith("/images") and len(self.remote["images"]) == 1:
                self.remote["images"].append({"listing_image_id": 1001, "rank": 2})
                return httpx.ReadTimeout("lost photo receipt")
        self.failure = lost
        self.assertEqual(self.save()["state"], "unknown")
        self.failure = None
        self.calls.clear()
        checked = self.client.post(self.base + "/check")
        self.assertEqual(checked.json()["state"], "draft", checked.text)
        self.assertTrue(checked.json()["etsy_photos_complete"])
        self.assertIsNone(checked.json()["etsy_pending_write"])
        self.assertTrue(self.calls)
        self.assertTrue(all(c[0] == "GET" for c in self.calls))
        self.calls.clear()
        self.assertEqual(self.save()["state"], "draft")
        self.assertFalse(any(c[3] for c in self.calls))

    def test_disconnected_account_fails_before_a_job_or_write(self):
        with patch.object(etsy_listings, "get_marketplace_accounts", return_value={}):
            r = self.client.post(self.base + "/draft", json={"revision_id": self.rev.id})
        self.assertEqual(r.status_code, 409)
        self.assertFalse(self.calls)
        self.assertFalse(JobRepo(self.db).list_all())

    def test_listing_profiles_expose_only_ids_and_titles(self):
        r = self.client.get("/api/etsy/listing-profiles")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"shipping": [{"id": "99", "title": "Buyer pays", "is_calculated": False}],
                                    "processing": [{"id": "88", "title": "Ready to ship · 1-2 days"}]})
        self.assertNotIn("never-expose", r.text)

    def test_another_shops_shipping_profile_cannot_create_a_draft(self):
        r = self.client.post(self.base + "/draft", json={"revision_id": self.rev.id, "shipping_profile_id": "999"})
        self.assertEqual(r.json()["state"], "not_saved", r.text)
        self.assertFalse(any(c[0] != "GET" for c in self.calls))

    def test_new_draft_requires_an_explicit_shipping_profile(self):
        self.rev = ListingRepo(self.db).save_revision(self.conv.id, {**LISTING, "etsy_specifics": {
            key: value for key, value in LISTING["etsy_specifics"].items() if key != "shipping_profile_id"}}, "user")
        self.assertEqual(self.save()["state"], "not_saved")
        self.assertFalse(any(c[0] != "GET" for c in self.calls))

    def test_calculated_shipping_requires_explicit_positive_package_measurements(self):
        self.calculated = True
        self.assertEqual(self.save()["state"], "not_saved")
        self.assertFalse(any(c[0] != "GET" for c in self.calls))
        package = {"item_weight": 8, "item_weight_unit": "oz", "item_length": 12, "item_width": 9, "item_height": 1, "item_dimensions_unit": "in"}
        r = self.client.post(self.base + "/draft", json={"revision_id": self.rev.id, "etsy_package": {**package, "item_weight": 0}})
        self.assertEqual(r.status_code, 422)
        r = self.client.post(self.base + "/draft", json={"revision_id": self.rev.id, "etsy_package": package})
        self.assertEqual(r.json()["state"], "draft", r.text)
        created = next(c[2] for c in self.calls if c[0] == "POST" and c[1].endswith("/listings"))
        self.assertEqual(created["item_weight"], "8.0")
        self.assertEqual(created["item_dimensions_unit"], "in")

    def test_variation_prices_are_preserved_instead_of_flattened(self):
        self.save()
        self.inventory["products"].append(copy.deepcopy(self.inventory["products"][0]))
        self.rev = ListingRepo(self.db).save_revision(self.conv.id, {**LISTING, "price": 13}, "user")
        self.calls.clear()
        saved = self.save()
        self.assertNotEqual(saved["revision_id"], self.rev.id)
        self.assertTrue(all(c[0] == "GET" for c in self.calls))

    def test_price_recovery_uses_inventory_when_listing_summary_is_stale(self):
        self.save()
        self.rev = ListingRepo(self.db).save_revision(self.conv.id, {**LISTING, "price": 13}, "user")
        def lost(method, path):
            if method == "PUT":
                self.inventory["products"][0]["offerings"][0]["price"]["amount"] = 1300
                return httpx.ReadTimeout("lost price receipt")
        self.failure = lost
        self.assertEqual(self.save()["state"], "unknown")
        self.assertEqual(self.remote["price"]["amount"], 1200)
        self.failure = None
        self.calls.clear()
        checked = self.client.post(self.base + "/check")
        self.assertEqual(checked.json()["state"], "draft", checked.text)
        self.assertTrue(all(c[0] == "GET" for c in self.calls))

    def test_invalid_or_ineligible_details_never_create_a_listing(self):
        for changes in [{"marketplace_category_ids": {}}, {"etsy_specifics": {}}, {"price": "NaN"}, {"price": "0.001"}]:
            with self.assertRaises(ValueError):
                etsy_listings.draft_fields({**LISTING, **changes}, creating=True)

    def test_a_recent_item_can_still_be_prepared_for_etsy(self):
        fields = etsy_listings.draft_fields({**LISTING, "etsy_specifics": {**LISTING["etsy_specifics"], "when_made": "2010 - 2019 (Recently)"}}, creating=True)
        self.assertEqual(fields["when_made"], "2010_2019")

    def test_validation_errors_show_field_names_without_raw_response_data(self):
        response = httpx.Response(400, json={"error": "shipping_profile_id is required; never-expose"},
                                  request=httpx.Request("POST", "https://api.etsy.com/"))
        error = httpx.HTTPStatusError("never-expose", request=response.request, response=response)
        message = etsy_listings._error_text(error)
        self.assertIn("shipping_profile_id", message)
        self.assertNotIn("never-expose", message)


class RelayMultipartTest(unittest.IsolatedAsyncioTestCase):
    async def test_form_and_photo_bytes_reach_the_relay_without_json_wrapping(self):
        requests = []
        async def handle(request):
            requests.append(request)
            return httpx.Response(201, json={"listing_image_id": 1})
        real_client = httpx.AsyncClient
        with patch.object(relay_client, "ensure_device", new_callable=AsyncMock, return_value={"token": "test-only"}), \
             patch.object(relay_client.httpx, "AsyncClient", side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs)):
            await relay_client.relay_request("POST", "/studio/etsy/v3/application/shops/123/listings/456/images",
                                             data={"rank": "1"}, files={"image": ("test.jpg", io.BytesIO(b"photo-bytes"), "image/jpeg")})
        self.assertIn("multipart/form-data; boundary=", requests[0].headers["content-type"])
        self.assertIn(b"photo-bytes", requests[0].content)
        self.assertIn(b'name="rank"', requests[0].content)
