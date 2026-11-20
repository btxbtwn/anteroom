"""A General Size the seller set (locked) is the size every unlocked marketplace slot and the title follow.

Generation rewrites the listing from the model's answer; ``restore_locked`` puts the
seller's General Size back at the end. Before this, the marketplace size slots, the
native form rows and the title kept the model's size, so the listing disagreed with itself.
"""
from __future__ import annotations

import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from worker_sessions import worker_sessions

from vendoo_studio.database import Base
from vendoo_studio.repositories.queries import ConversationRepo
from vendoo_studio.services import listing_persistence
from vendoo_studio.services.listing_generate import persist_generated_listing

MODEL = {
    "title": "M&O GOLD S Graphic T-Shirt Green Tie-Dye Regular",
    "brand": "M&O Gold",
    "description": "Graphic tee.",
    "price": 18,
    "size": "S",
}

EBAY_ROW = {"path": "ebay_specifics.Size", "label": "Size", "selection_only": True, "options": ["S", "M", "L"]}
MERCARI_ROW = {"path": "mercari_specifics.nativeSizeId", "label": "Size", "selection_only": True,
               "options": ["S", "M", "L"], "option_values": {"S": "11", "M": "12", "L": "13"}}


class LockedSizeGenerationTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.enterContext(worker_sessions(engine))
        self.db = sessionmaker(bind=engine)()
        self.addCleanup(self.db.close)
        self.conv = ConversationRepo(self.db).create(title="New Listing")

    def seller_set(self, size: str, *, locks=("size",), **extra) -> None:
        listing_persistence.save_revision(self.db, self.conv.id, {
            **MODEL, "size": size, "title": MODEL["title"].replace(" S ", f" {size} "),
            "seller_fields": list(locks), **extra,
        }, source="user_form")

    def generate(self, **extra) -> dict:
        return persist_generated_listing(self.db, self.conv.id, "", parsed={**MODEL, **extra})

    def test_slots_and_title_follow_the_locked_size(self):
        self.seller_set("L")
        parsed = self.generate(poshmark_specifics={"size": "S"}, mercari_specifics={"size": "S"},
                               depop_specifics={"size": "S"})
        self.assertEqual(parsed["size"], "L")
        self.assertEqual(parsed["title"], "M&O GOLD L Graphic T-Shirt Green Tie-Dye Regular")
        for market in ("poshmark", "mercari", "depop"):
            self.assertEqual(parsed[f"{market}_specifics"]["size"], "L", market)

    def test_a_marketplace_size_the_seller_locked_stays(self):
        self.seller_set("L", locks=("size", "depop_specifics.size"), depop_specifics={"size": "XS"})
        parsed = self.generate(poshmark_specifics={"size": "S"}, depop_specifics={"size": "S"})
        self.assertEqual(parsed["depop_specifics"]["size"], "XS")
        self.assertEqual(parsed["poshmark_specifics"]["size"], "L")

    def test_native_rows_take_the_locked_size_from_their_own_options(self):
        self.seller_set("L", _native_forms={"ebay": {"fields": [EBAY_ROW]}, "mercari": {"fields": [MERCARI_ROW]}},
                        ebay_specifics={"Size": "L"}, mercari_specifics={"nativeSizeId": "13"})
        parsed = self.generate(ebay_specifics={"Size": "S"}, mercari_specifics={"nativeSizeId": "11", "size": "S"})
        self.assertEqual(parsed["ebay_specifics"]["Size"], "L")
        self.assertEqual(parsed["mercari_specifics"]["nativeSizeId"], "13")

    def test_a_locked_size_the_native_form_does_not_offer_is_a_gap_not_free_text(self):
        self.seller_set("XL", _native_forms={"ebay": {"fields": [EBAY_ROW]}, "mercari": {"fields": [MERCARI_ROW]}})
        parsed = self.generate(ebay_specifics={"Size": "S"}, mercari_specifics={"nativeSizeId": "11"})
        self.assertEqual(parsed["size"], "XL")
        # eBay's Size and Mercari's size ID are chosen from options, which don't offer XL:
        # both are left empty (a gap for the seller), never the model's S, free text or another size.
        self.assertIn(parsed.get("ebay_specifics", {}).get("Size"), (None, ""))
        self.assertIn(parsed.get("mercari_specifics", {}).get("nativeSizeId"), (None, ""))

    def test_a_native_size_id_without_its_form_row_is_dropped_for_the_form_to_derive(self):
        self.seller_set("L")
        parsed = self.generate(poshmark_specifics={"size": "S", "nativeSizeId": "posh-S"})
        self.assertEqual(parsed["poshmark_specifics"]["size"], "L")
        self.assertNotIn("nativeSizeId", parsed["poshmark_specifics"])

    def test_mens_bottoms_keep_the_models_inseam_under_a_locked_waist(self):
        """32x30 from the model under a seller's 34: the waist is the seller's, the 30 inseam stays on eBay."""
        jeans = {"department": "Men", "category_path": "Men > Jeans", "title": "Levi's 32x30 Jeans 505 Blue"}
        self.seller_set("34", **{**jeans, "title": "Levi's 34 Jeans 505 Blue"})
        parsed = self.generate(**jeans, brand="Levi's", size="32x30")
        self.assertEqual(parsed["size"], "34")
        self.assertEqual(parsed["ebay_specifics"]["inseam"], "30")
        self.assertEqual(parsed["title"], "Levi's 34 Jeans 505 Blue")

    def test_a_multi_word_model_size_is_replaced_whole_in_the_title(self):
        self.seller_set("M", brand="Acme", title="Acme M Vintage Hat")
        parsed = self.generate(brand="Acme", size="One Size", title="Acme One Size Vintage Hat")
        self.assertEqual(parsed["title"], "Acme M Vintage Hat")

    def test_a_full_title_keeps_every_word_and_the_item_noun(self):
        """Swapping S for XXL would pass 80 characters. No word is cut, the last one is the item
        ("Tee"); only the overridden S goes, so the title never names a size against the lock."""
        title = "M&O GOLD S Graphic T-Shirt Green Tie-Dye Regular Fit Crew Neck Cotton Summer Tee"
        self.assertEqual(len(title), 80)
        self.seller_set("XXL")
        parsed = self.generate(title=title)
        self.assertEqual(parsed["size"], "XXL")
        self.assertEqual(parsed["title"], "M&O GOLD Graphic T-Shirt Green Tie-Dye Regular Fit Crew Neck Cotton Summer Tee")
        self.assertTrue(parsed["title"].endswith(" Tee"))
        self.assertNotIn(" S ", f" {parsed['title']} ")

    def test_a_title_with_room_takes_the_locked_size_and_keeps_its_item_noun(self):
        self.seller_set("XXL")
        parsed = self.generate(title="M&O GOLD S Graphic Tee")
        self.assertEqual(parsed["title"], "M&O GOLD XXL Graphic Tee")

    def test_native_rows_follow_even_when_the_models_general_size_agreed(self):
        row = {**EBAY_ROW, "options": ["30", "32", "34"]}
        jeans = {"department": "Men", "category_path": "Men > Jeans", "title": "Levi's 32 Jeans 505 Blue", "brand": "Levi's"}
        self.seller_set("32", _native_forms={"ebay": {"fields": [row]}}, ebay_specifics={"Size": "32"}, **jeans)
        # 32x30 normalizes to the locked 32, but the model's eBay row says 34.
        parsed = self.generate(size="32x30", ebay_specifics={"Size": "34"}, **{**jeans, "title": "Levi's 32x30 Jeans 505 Blue"})
        self.assertEqual(parsed["size"], "32")
        self.assertEqual(parsed["ebay_specifics"]["Size"], "32")

    def test_depop_kids_size_maps_with_the_saved_category_when_the_model_omits_it(self):
        row = {"path": "depop_specifics.size", "label": "Size", "selection_only": True, "options": ["10 years", "16 years"]}
        self.seller_set("M", _native_forms={"depop": {"fields": [row]}}, depop_specifics={"size": "10 years"},
                        direct_category_ids={"depop": "kidswear_jackets"})
        parsed = self.generate(size="XL (18/20)", depop_specifics={"size": "16 years"})
        self.assertEqual(parsed["size"], "M")
        self.assertEqual(parsed["depop_specifics"]["size"], "10 years")

    def test_mens_bottoms_inseam_reaches_the_native_ebay_inseam_row(self):
        rows = [{**EBAY_ROW, "options": ["30", "32", "34"]},
                {"path": "ebay_specifics.Inseam", "label": "Inseam", "selection_only": True, "options": ["30", "32"]}]
        jeans = {"department": "Men", "category_path": "Men > Jeans", "brand": "Levi's"}
        self.seller_set("32", _native_forms={"ebay": {"fields": rows}}, title="Levi's 32 Jeans", **jeans)
        parsed = self.generate(size="32x30", title="Levi's 32x30 Jeans", ebay_specifics={"Size": "34"}, **jeans)
        self.assertEqual(parsed["ebay_specifics"]["Size"], "32")
        self.assertEqual(parsed["ebay_specifics"]["Inseam"], "30")

    def test_an_inseam_the_seller_locked_stays(self):
        rows = [{**EBAY_ROW, "options": ["30", "32", "34"]},
                {"path": "ebay_specifics.Inseam", "label": "Inseam", "selection_only": True, "options": ["30", "32"]}]
        jeans = {"department": "Men", "category_path": "Men > Jeans", "brand": "Levi's"}
        self.seller_set("32", locks=("size", "ebay_specifics.Inseam"), _native_forms={"ebay": {"fields": rows}},
                        ebay_specifics={"Size": "32", "Inseam": "32"}, title="Levi's 32 Jeans", **jeans)
        parsed = self.generate(size="32x30", title="Levi's 32x30 Jeans", ebay_specifics={"Size": "34"}, **jeans)
        self.assertEqual(parsed["ebay_specifics"]["Inseam"], "32")

    def test_depop_maps_with_the_saved_category_over_a_conflicting_model_category(self):
        row = {"path": "depop_specifics.size", "label": "Size", "selection_only": True, "options": ["10 years", "16 years"]}
        self.seller_set("M", _native_forms={"depop": {"fields": [row]}}, depop_specifics={"size": "10 years"},
                        direct_category_ids={"depop": "kidswear_jackets"})
        parsed = self.generate(size="XL (18/20)", depop_specifics={"size": "16 years"},
                               direct_category_ids={"depop": "menswear_jackets"})
        self.assertEqual(parsed["depop_specifics"]["size"], "10 years")

    def test_ebay_native_size_is_spelled_with_the_sellers_locked_size_type(self):
        row = {**EBAY_ROW, "options": ["PM", "M"]}
        for size_type, expected in (("Petites", "PM"), ("Regular", "M")):
            with self.subTest(size_type=size_type):
                self.setUp()
                self.seller_set("M", locks=("size", "ebay_specifics.sizeType"), _native_forms={"ebay": {"fields": [row]}},
                                ebay_specifics={"Size": expected, "sizeType": size_type})
                other = "Regular" if size_type == "Petites" else "Petites"
                parsed = self.generate(size="M", ebay_specifics={"Size": "M" if expected == "PM" else "PM", "sizeType": other})
                self.assertEqual(parsed["ebay_specifics"]["Size"], expected)

    def test_another_marketplaces_locked_size_type_does_not_spell_ebays_size(self):
        row = {**EBAY_ROW, "options": ["PM", "M"]}
        self.seller_set("M", locks=("size", "mercari_specifics.sizeType"), _native_forms={"ebay": {"fields": [row]}},
                        mercari_specifics={"sizeType": "Petite"}, ebay_specifics={"Size": "M", "sizeType": "Regular"})
        parsed = self.generate(size="M", ebay_specifics={"Size": "PM", "sizeType": "Regular"})
        self.assertEqual(parsed["ebay_specifics"]["Size"], "M")

    def test_ebay_petite_size_matches_the_native_rows_own_option(self):
        """The category cache may list only M; the saved native row offering PM decides."""
        from unittest.mock import patch

        row = {**EBAY_ROW, "options": ["PM", "PL"]}
        self.seller_set("M", locks=("size", "ebay_specifics.sizeType"), _native_forms={"ebay": {"fields": [row]}},
                        ebay_specifics={"Size": "PM", "sizeType": "Petites"})
        with patch("vendoo_studio.services.listing_generate.ebay_size_choices", return_value=["M", "L"]):
            parsed = self.generate(size="M", ebay_specifics={"Size": "PL", "sizeType": "Petites"})
        self.assertEqual(parsed["ebay_specifics"]["Size"], "PM")

    def test_native_ebay_size_type_falls_back_after_stripping_blank_values(self):
        from vendoo_studio.services.listing_generate import _follow_native_size_rows

        row = {**EBAY_ROW, "options": ["PM", "M"]}
        seed = {"seller_fields": ["size"], "size": "M", "_native_forms": {"ebay": {"fields": [row]}}}
        for size_type, expected in (("", "PM"), ("   ", "PM"), (" Regular ", "M")):
            with self.subTest(size_type=size_type):
                listing = {"sizeType": "Petites", "ebay_specifics": {"Size": "L", "sizeType": size_type}}
                _follow_native_size_rows(listing, seed, "M")
                self.assertEqual(listing["ebay_specifics"]["Size"], expected)

    def test_a_blank_ebay_size_type_falls_back_to_the_general_size_type(self):
        row = {**EBAY_ROW, "options": ["PM", "M"]}
        self.seller_set("M", locks=("size", "sizeType"), sizeType="Petites", _native_forms={"ebay": {"fields": [row]}},
                        ebay_specifics={"Size": "PM"})
        parsed = self.generate(size="M", sizeType="Regular", ebay_specifics={"Size": "M", "sizeType": "   "})
        self.assertEqual(parsed["ebay_specifics"]["Size"], "PM")

    def test_without_a_lock_the_generated_size_stands(self):
        self.seller_set("L", locks=())
        parsed = self.generate(poshmark_specifics={"size": "S"})
        self.assertEqual(parsed["size"], "S")
        self.assertEqual(parsed["poshmark_specifics"]["size"], "S")

    def test_tag_native_size_ids_follow_the_tag_options_including_zero(self):
        row = {**MERCARI_ROW, "options": ["S", "M", "0"], "option_values": {"S": "11", "M": "12", "0": "10"}}
        for tag, expected in [("M", "12"), ("0", "10")]:
            with self.subTest(tag=tag):
                self.seller_set(tag, locks=(), _native_forms={"mercari": {"fields": [row]}})
                ConversationRepo(self.db).write_notes(self.conv.id, json.dumps({"size": tag}))
                parsed = self.generate(mercari_specifics={"nativeSizeId": "11"})
                self.assertEqual(parsed["size"], tag)
                self.assertEqual(parsed["mercari_specifics"]["nativeSizeId"], expected)

    def test_tag_size_unoffered_by_the_native_row_clears_the_models_id(self):
        self.seller_set("XL", locks=(), _native_forms={"mercari": {"fields": [MERCARI_ROW]}})
        ConversationRepo(self.db).write_notes(self.conv.id, json.dumps({"size": "XL"}))
        parsed = self.generate(mercari_specifics={"nativeSizeId": "11"})
        self.assertNotIn("nativeSizeId", parsed.get("mercari_specifics", {}))

    def test_tag_native_id_without_a_form_is_dropped_for_the_form_to_derive(self):
        ConversationRepo(self.db).write_notes(self.conv.id, json.dumps({"size": "M"}))
        parsed = self.generate(mercari_specifics={"size": "S", "nativeSizeId": "11"})
        self.assertEqual(parsed["mercari_specifics"]["size"], "M")
        self.assertNotIn("nativeSizeId", parsed["mercari_specifics"])

    def test_tag_alignment_preserves_a_native_id_the_seller_locked(self):
        self.seller_set("M", locks=("mercari_specifics.nativeSizeId",),
                        mercari_specifics={"nativeSizeId": "13"},
                        _native_forms={"mercari": {"fields": [MERCARI_ROW]}})
        ConversationRepo(self.db).write_notes(self.conv.id, json.dumps({"size": "M"}))
        parsed = self.generate(mercari_specifics={"nativeSizeId": "11"})
        self.assertEqual(parsed["size"], "M")
        self.assertEqual(parsed["mercari_specifics"]["nativeSizeId"], "13")

    def test_tag_replaces_the_models_whole_multiword_title_size(self):
        ConversationRepo(self.db).write_notes(self.conv.id, json.dumps({"size": "M"}))
        parsed = self.generate(brand="Acme", size="One Size", title="Acme One Size Cotton Top")
        self.assertEqual(parsed["title"], "Acme M Cotton Top")

    def test_tag_replaces_a_full_title_size_without_cutting_other_words(self):
        title = "M&O GOLD S Graphic T-Shirt Green Tie-Dye Regular Fit Crew Neck Cotton Summer Tee"
        self.assertEqual(len(title), 80)
        ConversationRepo(self.db).write_notes(self.conv.id, json.dumps({"size": "XXL"}))
        parsed = self.generate(title=title)
        self.assertEqual(parsed["title"], title.replace(" S ", " "))
        self.assertTrue(parsed["title"].endswith(" Tee"))

    def test_tag_alignment_keeps_a_saved_legacy_mercari_size_effective(self):
        row = {**MERCARI_ROW, "options": ["S", "M", "L", "0"],
               "option_values": {**MERCARI_ROW["option_values"], "0": "10"}}
        for saved, expected in [("L", "13"), ("0", "10")]:
            with self.subTest(saved=saved):
                self.seller_set("M", locks=("mercari_specifics.size",), mercari_specifics={"size": saved},
                                _native_forms={"mercari": {"fields": [row]}})
                ConversationRepo(self.db).write_notes(self.conv.id, json.dumps({"size": "M"}))
                parsed = self.generate(mercari_specifics={"size": "S"})
                block = parsed["mercari_specifics"]
                effective = block.get("nativeSizeId") or row["option_values"].get(block.get("size"))
                self.assertEqual(block["size"], saved)
                self.assertEqual(effective, expected)


if __name__ == "__main__":
    unittest.main()
