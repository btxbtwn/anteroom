import json
import unittest

import httpx

from release_smoke import verify

ACCOUNT = {"user_id": "test-seller", "email": "smoke@example.invalid", "password": "fixture"}


class ReleaseSmokeTests(unittest.TestCase):
    def run_seller(self, user="test-seller", stale=False, unrelated=False, connected=False):
        rows = [{"id": "other", "title": "Seller item"}] if unrelated else []
        self.calls = []

        def respond(request):
            path = request.url.path
            self.calls.append((request.method, path))
            if path.startswith("/auth/"):
                self.assertNotIn("Authorization", request.headers)
                return httpx.Response(200, json={"access_token": "session", "user": {"id": user}})
            self.assertEqual(request.headers["Authorization"], "Bearer session")
            if path == "/api/marketplace-accounts":
                return httpx.Response(200, json={"accounts": {
                    name: {"connected": connected, "pending": False} for name in ("ebay", "etsy")
                }})
            if path == "/api/marketplaces/sessions":
                return httpx.Response(200, json={"chrome_connected": False, "sessions": {}})
            if path == "/api/conversations":
                if request.method == "GET":
                    return httpx.Response(200, json=rows)
                row = dict(json.loads(request.content), id="beef12345678", status="draft")
                rows.append(row)
            elif path == "/api/conversations/beef12345678":
                row = rows[0]
                if request.method == "PATCH" and not stale:
                    row.update(json.loads(request.content))
            else:
                self.fail(f"Unexpected request {request.method} {path}")
            return httpx.Response(200, json=row)

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            verify(client, ACCOUNT, "public-key")
            verify(client, ACCOUNT, "public-key")

    def test_persists_and_reuses_only_one_draft(self):
        self.run_seller()
        self.assertEqual(self.calls.count(("POST", "/api/conversations")), 1)
        self.assertEqual(self.calls.count(("GET", "/api/conversations/beef12345678")), 2)

    def test_refuses_wrong_identity_before_inventory(self):
        with self.assertRaisesRegex(RuntimeError, "identity"):
            self.run_seller(user="other")
        self.assertEqual(len(self.calls), 1)

    def test_refuses_other_inventory_before_writing(self):
        with self.assertRaisesRegex(RuntimeError, "inventory"):
            self.run_seller(unrelated=True)
        self.assertEqual(len(self.calls), 4)

    def test_refuses_connected_marketplace_before_inventory(self):
        with self.assertRaisesRegex(RuntimeError, "marketplace connection"):
            self.run_seller(connected=True)
        self.assertEqual(len(self.calls), 2)

    def test_detects_lost_save(self):
        with self.assertRaisesRegex(RuntimeError, "readback"):
            self.run_seller(stale=True)
