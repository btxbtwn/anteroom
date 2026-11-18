"""A General Size the seller set (locked) is the size every unlocked marketplace slot and the title follow.

Generation rewrites the listing from the model's answer; ``restore_locked`` puts the
seller's General Size back at the end. Before this, the marketplace size slots, the
native form rows and the title kept the model's size, so the listing disagreed with itself.
"""
from __future__ import annotations

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

    def test_a_title_over_80_characters_drops_its_last_words_not_the_size(self):
        title = "M&O GOLD S Graphic T-Shirt Green Tie-Dye Regular Fit Crew Neck Cotton Summer Tee"
        self.assertEqual(len(title), 80)
        self.seller_set("XXL")
        parsed = self.generate(title=title)
        self.assertTrue(parsed["title"].startswith("M&O GOLD XXL Graphic T-Shirt"), parsed["title"])
        self.assertLessEqual(len(parsed["title"]), 80)

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

    def test_without_a_lock_the_generated_size_stands(self):
        self.seller_set("L", locks=())
        parsed = self.generate(poshmark_specifics={"size": "S"})
        self.assertEqual(parsed["size"], "S")
        self.assertEqual(parsed["poshmark_specifics"]["size"], "S")


if __name__ == "__main__":
    unittest.main()
