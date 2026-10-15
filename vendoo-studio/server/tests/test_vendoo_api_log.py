"""Vendoo API log ring and the Settings routes that read it."""

from __future__ import annotations

import json
import unittest

from fastapi.testclient import TestClient

from vendoo_studio.main import app
from vendoo_studio.services.vendoo_api_log import clear, ingest, list_entries, record, sanitize_entry

JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1MSJ9.c2lnbmF0dXJl"


def _entry(**overrides):
    entry = {
        "method": "POST",
        "url": "https://api.web.vendoo.co/api/item/abc?userId=u1&key=AIzaSECRETKEY#frag",
        "status": 200,
        "ok": True,
        "duration_ms": 12,
        "at": "2026-09-27T04:00:00Z",
        "request_headers": {"Authorization": f"Bearer {JWT}", "Content-Type": "application/json"},
        "request_body": json.dumps({"title": "Levi 501", "refresh_token": "REFRESHSECRET"}),
        "response_headers": {"content-type": "application/json", "set-cookie": "sid=COOKIESECRET"},
        "response_body": json.dumps({"id": "abc", "idToken": JWT}),
    }
    entry.update(overrides)
    return entry


class SanitizeTest(unittest.TestCase):
    def tearDown(self):
        clear()

    def test_keeps_the_full_request_and_masks_credentials(self):
        entry = sanitize_entry(_entry())
        self.assertIsNotNone(entry)
        self.assertEqual(entry["url"], "https://api.web.vendoo.co/api/item/abc?userId=u1&key=AIza…(masked)")
        self.assertEqual(entry["host"], "api.web.vendoo.co")
        self.assertEqual(entry["path"], "/api/item/abc")
        self.assertEqual(entry["request_headers"]["Authorization"], "Bearer …(masked)")
        self.assertEqual(entry["request_headers"]["Content-Type"], "application/json")
        self.assertEqual(entry["response_headers"]["set-cookie"], "…(masked)")
        self.assertIn('"title": "Levi 501"', entry["request_body"])
        self.assertIn('"id": "abc"', entry["response_body"])
        blob = json.dumps(entry)
        for secret in ("AIzaSECRETKEY", "REFRESHSECRET", "COOKIESECRET", JWT):
            self.assertNotIn(secret, blob)

    def test_failures_keep_their_error_text(self):
        entry = sanitize_entry(_entry(status=500, ok=False, error=f"createItem failed for Bearer {JWT}"))
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["error"], "createItem failed for Bearer …(masked)")
        self.assertEqual(sanitize_entry(_entry(status=None, ok=False))["error"], "failed")

    def test_signed_upload_signature_and_userinfo(self):
        signed = sanitize_entry(_entry(
            method="PUT",
            url="https://storage.googleapis.com/bucket/object?X-Goog-Signature=abcdef123&X-Goog-Date=20260927",
        ))
        self.assertEqual(
            signed["url"],
            "https://storage.googleapis.com/bucket/object?X-Goog-Signature=abcd…(masked)&X-Goog-Date=20260927",
        )
        self.assertIsNone(sanitize_entry(_entry(url="https://user:secret@api.web.vendoo.co/api/item/abc")))
        self.assertIsNone(sanitize_entry(_entry(url="javascript:alert(1)")))

    def test_bodies_are_capped(self):
        entry = sanitize_entry(_entry(response_body="a" * 200_000))
        self.assertLess(len(entry["response_body"]), 70_000)

    def test_ring_keeps_the_newest_two_hundred(self):
        record([_entry(url=f"https://api.web.vendoo.co/api/item/{i}") for i in range(205)])
        entries = list_entries()
        self.assertEqual(len(entries), 200)
        self.assertEqual(entries[0]["path"], "/api/item/204")
        self.assertEqual(entries[-1]["path"], "/api/item/5")
        self.assertNotIn("response_body", entries[0])

    def test_ingest_reads_a_batched_payload(self):
        stored = ingest({"entries": [_entry(url="https://securetoken.googleapis.com/v1/token"), "not-an-entry"]})
        self.assertEqual(stored, 1)
        self.assertEqual(list_entries()[0]["host"], "securetoken.googleapis.com")


class RouteTest(unittest.TestCase):
    def setUp(self):
        clear()
        self.client = TestClient(app)

    def tearDown(self):
        clear()

    def test_list_detail_and_clear(self):
        record([_entry()])
        listed = self.client.get("/api/settings/vendoo-api-logs")
        self.assertEqual(listed.status_code, 200)
        summary = listed.json()["entries"][0]
        self.assertEqual(summary["url"], "https://api.web.vendoo.co/api/item/abc?userId=u1&key=AIza…(masked)")
        self.assertNotIn("request_body", summary)
        detail = self.client.get(f"/api/settings/vendoo-api-logs/{summary['id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("Levi 501", detail.json()["request_body"])
        for secret in ("AIzaSECRETKEY", "REFRESHSECRET", JWT):
            self.assertNotIn(secret, detail.text)
        self.assertEqual(self.client.get("/api/settings/vendoo-api-logs/999999").status_code, 404)
        cleared = self.client.delete("/api/settings/vendoo-api-logs")
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(self.client.get("/api/settings/vendoo-api-logs").json()["entries"], [])
