"""Vendoo stays on until Settings turns it off. Off refuses Vendoo calls."""

from __future__ import annotations

import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from vendoo_studio.main import app
from vendoo_studio.services import user_settings, vendoo_bulk_import, vendoo_label_sync
from vendoo_studio.services.schema_probe import maybe_start_schema_probe
from vendoo_studio.services.vendoo_gate import vendoo_request_blocked


class VendooModeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._data = os.environ.get("VENDOO_STUDIO_DATA_DIR")
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["VENDOO_STUDIO_DATA_DIR"] = self.tmp.name
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.tmp.cleanup()
        if self._data is None:
            os.environ.pop("VENDOO_STUDIO_DATA_DIR", None)
        else:
            os.environ["VENDOO_STUDIO_DATA_DIR"] = self._data

    def test_defaults_on_and_can_be_turned_off(self):
        self.assertTrue(user_settings.vendoo_enabled())
        saved = self.client.put("/api/settings/ui", json={"vendoo_enabled": False})
        self.assertEqual(saved.status_code, 200)
        self.assertFalse(saved.json()["vendoo_enabled"])
        self.assertFalse(user_settings.vendoo_enabled())

    def test_off_blocks_vendoo_routes_and_leaves_direct_routes(self):
        self.client.put("/api/settings/ui", json={"vendoo_enabled": False})
        blocked = self.client.post("/api/jobs/send", json={})
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("Vendoo is off", blocked.json()["detail"])
        sync = self.client.post("/api/catalog/sync")
        self.assertEqual(sync.status_code, 409)
        accounts = self.client.get("/api/marketplace-accounts")
        self.assertNotEqual(accounts.status_code, 409)
        self.assertEqual(self.client.get("/api/settings/ui").status_code, 200)

    def test_gate_matrix(self):
        self.assertTrue(vendoo_request_blocked("POST", "/api/jobs/send"))
        self.assertTrue(vendoo_request_blocked("POST", "/api/jobs/job-1/open"))
        self.assertTrue(vendoo_request_blocked("POST", "/api/jobs/job-1/browser/open"))
        self.assertTrue(vendoo_request_blocked("POST", "/api/imports/vendoo/bulk"))
        self.assertTrue(vendoo_request_blocked("GET", "/api/imports/vendoo/label-names"))
        self.assertTrue(vendoo_request_blocked("POST", "/api/imports/vendoo/label-names"))
        self.assertTrue(vendoo_request_blocked("POST", "/api/catalog/sync"))
        self.assertTrue(vendoo_request_blocked("GET", "/api/conversations/vendoo-link-duplicates"))
        self.assertFalse(vendoo_request_blocked("GET", "/api/catalog/sync"))
        self.assertFalse(vendoo_request_blocked("POST", "/api/marketplace-listings/delist"))
        self.assertFalse(vendoo_request_blocked("GET", "/api/jobs/queue"))

    def test_off_does_not_start_import_labels_or_schema_probe(self):
        user_settings.set_ui_prefs(vendoo_enabled=False)
        started = vendoo_bulk_import.start()
        self.assertFalse(started["running"])
        self.assertEqual(started["reason"], "vendoo off")
        labels = vendoo_label_sync.start(force=True)
        self.assertFalse(labels["running"])
        self.assertEqual(labels["reason"], "vendoo off")
        self.assertEqual(maybe_start_schema_probe(None, "conv")["reason"], "vendoo off")
