"""Seller-owned listing values: prices that follow, clears that stay, Depop measurements."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from vendoo_studio.models.depop_fields import depop_size_for, ensure_depop_category_optionals
from vendoo_studio.models.validation import normalize_listing_dropdowns
from vendoo_studio.services.chat_listing import _field_commands
from vendoo_studio.services.department_review import _recommendation, review_department
from vendoo_studio.services.depop_measurements import depop_measurements_from_notes, sync_depop_measurements
from vendoo_studio.services.direct_preflight import gap, with_fix
from vendoo_studio.services.error_log import install, list_entries, query_entries
from vendoo_studio.services.native_marketplaces import _depop_photos
from vendoo_studio.services.registry import _leaf_named_by_listing
from vendoo_studio.services.seller_overrides import (
    apply_field_updates,
    materialize_prices,
    prepare_user_listing,
    preserving_locks,
    price_for,
)


NOTES = {
    "garment": "top",
    "measurements": {"top": {"pitToPit": "22", "length": "26", "sleeve": "23"}},
}


def _prices(general, marketplace):
    return {
        "price": general,
        "ebay_specifics": {"price": marketplace},
        "poshmark_specifics": {"poshmarkPrice": marketplace},
        "mercari_specifics": {"price": marketplace},
        "depop_specifics": {"price": marketplace},
    }


class PriceFollowTest(unittest.TestCase):
    def test_a_new_general_price_replaces_the_recommended_price_on_every_marketplace(self):
        # Recommended price was $23. The seller set the item price to $29.99
        # while each marketplace row still showed $23.
        previous = _prices(23, 23)
        submitted = _prices(29.99, 23)
        prepare_user_listing(previous, submitted)
        self.assertEqual(submitted["price"], 29.99)
        self.assertEqual(submitted["ebay_specifics"]["price"], 29.99)
        self.assertEqual(submitted["poshmark_specifics"]["poshmarkPrice"], 29.99)
        self.assertEqual(submitted["mercari_specifics"]["price"], 29.99)
        self.assertEqual(submitted["depop_specifics"]["price"], 29.99)
        locks = set(submitted.get("seller_fields") or [])
        self.assertIn("price", locks)
        for path in (
            "ebay_specifics.price",
            "poshmark_specifics.poshmarkPrice",
            "mercari_specifics.price",
            "depop_specifics.price",
        ):
            self.assertNotIn(path, locks)
        materialize_prices(submitted)
        self.assertEqual(price_for(submitted, "ebay"), 29.99)
        self.assertEqual(price_for(submitted, "poshmark"), 29.99)
        self.assertEqual(price_for(submitted, "depop"), 29.99)

    def test_an_explicit_marketplace_price_stays_when_the_item_price_changes(self):
        previous = _prices(23, 23)
        previous["poshmark_specifics"]["poshmarkPrice"] = 25
        submitted = _prices(29.99, 23)
        submitted["poshmark_specifics"]["poshmarkPrice"] = 25
        prepare_user_listing(previous, submitted)
        self.assertEqual(submitted["ebay_specifics"]["price"], 29.99)
        self.assertEqual(submitted["poshmark_specifics"]["poshmarkPrice"], 25)
        self.assertIn("poshmark_specifics.poshmarkPrice", submitted["seller_fields"])
        self.assertEqual(price_for(submitted, "poshmark"), 25)
        self.assertEqual(price_for(submitted, "ebay"), 29.99)

    def test_typing_a_different_marketplace_price_locks_it(self):
        previous = _prices(23, 23)
        submitted = _prices(29.99, 23)
        submitted["poshmark_specifics"]["poshmarkPrice"] = 25
        prepare_user_listing(previous, submitted)
        self.assertEqual(submitted["poshmark_specifics"]["poshmarkPrice"], 25)
        self.assertIn("poshmark_specifics.poshmarkPrice", submitted["seller_fields"])

    def test_setting_a_marketplace_price_back_to_the_item_price_unlocks_it(self):
        previous = _prices(23, 25)
        previous["seller_fields"] = ["poshmark_specifics.poshmarkPrice"]
        submitted = _prices(29.99, 29.99)
        submitted["seller_fields"] = ["poshmark_specifics.poshmarkPrice"]
        prepare_user_listing(previous, submitted)
        self.assertEqual(submitted["poshmark_specifics"]["poshmarkPrice"], 29.99)
        self.assertNotIn("poshmark_specifics.poshmarkPrice", submitted.get("seller_fields") or [])

    def test_a_stale_unlocked_marketplace_price_is_rewritten_before_send(self):
        listing = {"price": 29.99, "ebay_specifics": {"price": 23}, "depop_specifics": {"price": 23}}
        materialize_prices(listing)
        self.assertEqual(listing["ebay_specifics"]["price"], 29.99)
        self.assertEqual(price_for(listing, "depop"), 29.99)


class DepopStyleClearTest(unittest.TestCase):
    def test_a_comma_joined_style_becomes_separate_choices(self):
        listing = {"depop_specifics": {"style": ["Casual, Minimalist, Indie"]}}
        from vendoo_studio.services.seller_overrides import expand_depop_multi

        self.assertTrue(expand_depop_multi(listing))
        self.assertEqual(listing["depop_specifics"]["style"], ["Casual", "Minimalist", "Indie"])

    def test_clearing_style_stays_empty_through_cleanup(self):
        previous = {
            "title": "Fleece jacket",
            "description": "Warm",
            "depop_specifics": {"style": ["Casual, Minimalist, Indie"]},
        }
        submitted = {
            "title": "Fleece jacket",
            "description": "Warm",
            "depop_specifics": {"style": []},
        }
        prepare_user_listing(previous, submitted)

        def cleanup(listing):
            ensure_depop_category_optionals(listing)
            normalize_listing_dropdowns(listing)

        preserving_locks(submitted, cleanup)
        self.assertEqual(submitted["depop_specifics"]["style"], [])
        self.assertIn("depop_specifics.style", submitted["seller_fields"])

    def test_a_field_command_clears_style_and_sets_sku_and_mercari(self):
        text = '{"fields":[{"path":"depop_specifics.style","clear":true},{"path":"sku","value":"A21"},{"path":"mercari_specifics.subcategory","value":"Jackets"}]}'
        commands = _field_commands(text)
        listing = {"sku": "A20", "depop_specifics": {"style": ["Casual", "Minimalist", "Indie"]}, "mercari_specifics": {}}
        apply_field_updates(listing, commands)
        self.assertEqual(listing["depop_specifics"]["style"], [])
        self.assertEqual(listing["sku"], "A21")
        self.assertEqual(listing["mercari_specifics"]["subcategory"], "Jackets")
        self.assertIn("depop_specifics.style", listing["seller_fields"])
        self.assertIn("sku", listing["seller_fields"])

        def cleanup(item):
            ensure_depop_category_optionals(item)
            normalize_listing_dropdowns(item)

        preserving_locks(listing, cleanup)
        self.assertEqual(listing["depop_specifics"]["style"], [])
        self.assertEqual(listing["sku"], "A21")


class DepopMeasurementsTest(unittest.TestCase):
    def test_stored_inches_become_depop_rows(self):
        block = depop_measurements_from_notes(NOTES)
        self.assertEqual(block["unit"], "in")
        self.assertEqual(
            [(row["id"], row["label"], row["value"]) for row in block["values"]],
            [("pit_to_pit", "Pit to pit", "22"), ("length", "Length", "26"), ("sleeve", "Sleeve", "23")],
        )
        listing = {"depop_specifics": {}}
        self.assertTrue(sync_depop_measurements(listing, NOTES))
        listing["seller_fields"] = ["depop_specifics.measurements"]
        listing["depop_specifics"]["measurements"]["values"][0]["value"] = "21"
        self.assertFalse(sync_depop_measurements(listing, NOTES))
        self.assertEqual(listing["depop_specifics"]["measurements"]["values"][0]["value"], "21")

    def test_a_seller_measurement_edit_is_kept(self):
        previous = {"depop_specifics": {"measurements": depop_measurements_from_notes(NOTES)}}
        edited = depop_measurements_from_notes(NOTES)
        edited["values"][0]["value"] = "21"
        submitted = {"depop_specifics": {"measurements": edited}}
        prepare_user_listing(previous, submitted, notes=NOTES)
        self.assertEqual(submitted["depop_specifics"]["measurements"]["values"][0]["value"], "21")
        self.assertIn("depop_specifics.measurements", submitted["seller_fields"])


class PetiteAndJacketTest(unittest.TestCase):
    def test_petite_medium_is_depop_size_m(self):
        self.assertEqual(depop_size_for("PM", {"sizeType": "Petites"}), "M")
        self.assertEqual(depop_size_for("PM", {"sizeType": "Regular"}), "PM")

    def test_a_vest_leaf_does_not_match_a_jacket_under_that_ebay_type(self):
        jacket = {"title": "Full Zip Fleece Jacket", "ebay_specifics": {"type": "Coats, Jackets & Vests"}}
        self.assertFalse(_leaf_named_by_listing("Vests", jacket))
        vest = {"title": "Black Vest", "ebay_specifics": {"type": "Coats, Jackets & Vests"}}
        self.assertTrue(_leaf_named_by_listing("Vests", vest))


class DepopPhotoCapTest(unittest.TestCase):
    def test_nine_photos_send_the_first_eight_with_a_warning(self):
        photos = [SimpleNamespace(id=str(index)) for index in range(9)]
        chosen, warning = _depop_photos({}, photos)
        self.assertEqual([photo.id for photo in chosen], [str(index) for index in range(8)])
        self.assertIn("8", warning)

    def test_a_photo_gap_names_the_command_that_fixes_it(self):
        item = with_fix(gap("marketplace_photos.depop", "Depop keeps 8 photos.", blocking=False))
        self.assertFalse(item["blocking"])
        self.assertIn("anteroom conversations fields", item["fix"]["command"])
        self.assertIn(item["fix"]["command"], item["message"])


class DepartmentAndLogsTest(unittest.TestCase):
    def test_department_recommendation_reads_the_analysis(self):
        found = _recommendation(
            "Department: Women\nmodel confidence (not verified): 0.8\nEvidence for department: buttons on the left."
        )
        self.assertEqual(found["department"], "Women")
        self.assertEqual(found["confidence"], 0.8)
        self.assertIn("buttons", found["reasoning"])

    def test_department_review_asks_for_a_model_when_none_is_connected(self):
        db = MagicMock()
        with patch("vendoo_studio.repositories.queries.ConversationRepo") as repo, \
             patch("vendoo_studio.services.listing_generate.latest_photo_analysis", return_value=""), \
             patch("vendoo_studio.services.listing_provider.get_listing_provider", return_value=None):
            repo.return_value.get.return_value = object()
            repo.return_value.get_messages.return_value = []
            result = asyncio.run(review_department(db, "conv"))
        self.assertFalse(result["ok"])
        self.assertIn("Connect a model", result["error"])

    def test_disk_logs_keep_info_and_the_warning_ring_does_not(self):
        previous = os.environ.get("VENDOO_STUDIO_DATA_DIR")
        tmp = tempfile.TemporaryDirectory()
        os.environ["VENDOO_STUDIO_DATA_DIR"] = tmp.name
        try:
            install()
            log = logging.getLogger("vendoo_studio.listing_generate")
            log.info("not kept")
            log.warning("photo analysis failed")
            self.assertNotIn("not kept", [entry["message"] for entry in list_entries()])
            info = query_entries(level="info", grep="not kept")
            self.assertEqual([entry["message"] for entry in info], ["not kept"])
            warnings = query_entries(level="warning", grep="photo analysis")
            self.assertEqual(warnings[0]["message"], "photo analysis failed")
            self.assertEqual(query_entries(level="error", grep="not kept"), [])
        finally:
            tmp.cleanup()
            if previous is None:
                os.environ.pop("VENDOO_STUDIO_DATA_DIR", None)
            else:
                os.environ["VENDOO_STUDIO_DATA_DIR"] = previous
