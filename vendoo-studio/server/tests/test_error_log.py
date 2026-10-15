import logging
import unittest

from fastapi.testclient import TestClient

from vendoo_studio.main import app
from vendoo_studio.services import error_log


class ErrorLogTest(unittest.TestCase):
    def setUp(self):
        error_log.install()
        error_log.clear()

    def tearDown(self):
        error_log.clear()

    def test_keeps_warnings_and_errors_newest_first(self):
        log = logging.getLogger("vendoo_studio.listing_generate")
        log.info("not kept")
        log.warning("photo analysis failed: %s", "Invalid User API Key")
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            log.exception("listing generation crashed")

        entries = error_log.list_entries()
        self.assertEqual([e["message"] for e in entries], [
            "listing generation crashed",
            "photo analysis failed: Invalid User API Key",
        ])
        self.assertEqual(entries[0]["level"], "error")
        self.assertEqual(entries[0]["source"], "listing_generate")
        self.assertIn("RuntimeError: boom", entries[0]["detail"])
        self.assertIsNone(entries[1]["detail"])

    def test_masks_secrets(self):
        logging.getLogger("vendoo_studio.cursor").warning("request failed with api_key=cursor_supersecret123")
        message = error_log.list_entries()[0]["message"]
        self.assertNotIn("supersecret", message)

    def test_install_is_idempotent(self):
        error_log.install()
        logging.getLogger("vendoo_studio").warning("once")
        self.assertEqual(len(error_log.list_entries()), 1)

    def test_settings_routes_list_and_clear(self):
        logging.getLogger("vendoo_studio").warning("shown in settings")
        client = TestClient(app)
        entries = client.get("/api/settings/error-logs").json()["entries"]
        self.assertEqual(entries[0]["message"], "shown in settings")
        self.assertEqual(client.delete("/api/settings/error-logs").json(), {"ok": True})
        self.assertEqual(client.get("/api/settings/error-logs").json(), {"entries": []})
