from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from vendoo_studio.main import app
from vendoo_studio.services import marketplaces


class MarketplaceSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._data = os.environ.get("VENDOO_STUDIO_DATA_DIR")
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["VENDOO_STUDIO_DATA_DIR"] = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()
        if self._data is None:
            os.environ.pop("VENDOO_STUDIO_DATA_DIR", None)
        else:
            os.environ["VENDOO_STUDIO_DATA_DIR"] = self._data

    def test_defaults_to_original_five(self):
        self.assertEqual(marketplaces.get_selected_marketplaces(), marketplaces.DEFAULT_SELECTED)
        self.assertEqual(marketplaces.selected_fillable_platforms(), ["ebay", "etsy", "poshmark", "mercari", "depop"])

    def test_persists_direct_marketplaces_in_catalog_order(self):
        selected = marketplaces.set_selected_marketplaces(["mercari", "ebay", "ebay", "depop", "poshmark"])
        self.assertEqual(selected, ["ebay", "poshmark", "mercari", "depop"])
        self.assertEqual(marketplaces.get_selected_marketplaces(), ["ebay", "poshmark", "mercari", "depop"])
        # Category-field generation covers the configured forms.
        self.assertEqual(marketplaces.selected_fillable_platforms(), ["ebay", "poshmark", "mercari", "depop"])


    def test_a_saved_choice_keeps_its_marketplaces_when_one_is_dropped(self):
        marketplaces.set_selected_marketplaces(["ebay"])
        payload = json.loads(Path(self.tmp.name, "settings.json").read_text())
        payload["marketplaces"] = ["ebay", "facebook", "mercari", "not-a-market"]
        Path(self.tmp.name, "settings.json").write_text(json.dumps(payload))
        self.assertEqual(marketplaces.get_selected_marketplaces(), ["ebay", "mercari"])
        self.assertEqual(marketplaces.selected_fillable_platforms(), ["ebay", "mercari"])

    def test_grailed_is_no_longer_available(self):
        with self.assertRaises(ValueError):
            marketplaces.set_selected_marketplaces(["grailed"])
        self.assertNotIn("grailed", marketplaces.KNOWN_MARKETPLACES)



    def test_catalog_covers_direct_listing_marketplaces(self):
        for marketplace_id in ("ebay", "etsy", "poshmark", "mercari", "depop"):

            self.assertIn(marketplace_id, marketplaces.KNOWN_MARKETPLACES)

    def test_rejects_unknown_on_write(self):
        for retired in ("not-a-market", "facebook"):
            with self.assertRaises(ValueError):
                marketplaces.set_selected_marketplaces(["ebay", retired])

    def test_empty_selection_is_general_only(self):
        self.assertEqual(marketplaces.set_selected_marketplaces([]), [])
        self.assertEqual(marketplaces.selected_fillable_platforms(), [])


class MarketplaceSettingsRouteTest(unittest.TestCase):
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

    def test_get_and_put_marketplaces(self):
        empty = self.client.get("/api/settings/marketplaces")
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["selected"], marketplaces.DEFAULT_SELECTED)
        self.assertEqual(empty.json()["fillable"], marketplaces.DEFAULT_SELECTED)
        self.assertEqual([item["id"] for item in empty.json()["available"]], list(marketplaces.KNOWN_MARKETPLACES))

        saved = self.client.put("/api/settings/marketplaces", json={"selected": ["poshmark", "poshmark"]})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["selected"], ["poshmark"])
        self.assertEqual(saved.json()["fillable"], ["poshmark"])

        crosslist = self.client.put(
            "/api/settings/marketplaces",
            json={"selected": ["poshmark", "mercari"]},
        )
        self.assertEqual(crosslist.status_code, 200)
        self.assertEqual(crosslist.json()["selected"], ["poshmark", "mercari"])
        self.assertEqual(crosslist.json()["fillable"], ["poshmark", "mercari"])

        rejected = self.client.put("/api/settings/marketplaces", json={"selected": ["ebay", "nope"]})
        self.assertEqual(rejected.status_code, 400)


if __name__ == "__main__":
    unittest.main()
