"""The listing assistant stores the same last-step category the form dropdown saves."""

from __future__ import annotations

import inspect
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from vendoo_studio.database import Base
from vendoo_studio.models.catalog import CategoryTree, CategoryTreeNode
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services.agent_categories import (
    MARKETS,
    apply_agent_categories,
    apply_saved_agent_category,
    map_general_category,
    markets_for_choice,
    requested_choices,
)
from vendoo_studio.services.chat_prompts import build_chat_messages, current_listing_prompt

DEPT = "dept"
SHIRTS = "shirts"
TEES = "tees"
SHOES = "shoes"
POSHMARK = {"catalog": {"departments": [{"id": DEPT, "display": "Men", "categories": [
    {"id": SHIRTS, "display": "Shirts", "category_features": [
        {"id": TEES, "display": "Tees"},
        {"id": "long", "display": "Tees - Long Sleeve"},
    ]},
    {"id": SHOES, "display": "Shoes", "category_features": []},
]}]}}
DEPOP = {
    "department": [{"id": "menswear", "name": "Men"}, {"id": "womenswear", "name": "Women"}],
    "group": [{
        "id": "tops", "name": "Tops", "department": ["menswear"],
        "product_types": [{"id": "tees", "name": "Tees"}],
    }],
}
EBAY = [{"id": "15687", "path": "Clothing > Men > Shirts > Tees"}, {"id": "15709", "path": "Clothing > Men > Shoes"}]
MERCARI = [
    {"id": "1877", "path": "Women > Tops", "parent": True},
    {"id": "1911", "path": "Women > Tops > Tees"},
]
CATALOGS = {"poshmark": POSHMARK, "depop": DEPOP, "ebay": EBAY, "mercari": MERCARI}
TEE_PATH = "Men > Shirts > Tees"


def choose(listing, *, requested=None, markets=None, explicit=None, catalogs=None):
    return apply_agent_categories(
        listing,
        CATALOGS if catalogs is None else catalogs,
        requested=requested if requested is not None else {"category_path": TEE_PATH},
        markets=set(MARKETS) if markets is None else markets,
        explicit=explicit or set(),
    )


class AgentCategoryChoiceTest(unittest.TestCase):
    def test_a_terminal_path_sets_each_marketplace_category(self):
        listing = {"title": "Tee", "category_path": TEE_PATH}
        written = choose(listing)
        self.assertEqual(listing["direct_category_ids"]["poshmark"], f"{DEPT}/{SHIRTS}/{TEES}")
        self.assertEqual(listing["direct_category_ids"]["depop"], "menswear_tees")
        self.assertEqual(listing["direct_category_ids"]["ebay"], "15687")
        self.assertEqual(listing["direct_category_ids"]["ebay_path"], "Clothing > Men > Shirts > Tees")
        self.assertNotIn("mercari", listing["direct_category_ids"])
        self.assertIn("direct_category_ids.poshmark", written)
        self.assertIn("sellwild_specifics.nativeCategoryId", written)

    def test_a_new_category_replaces_an_older_marketplace_path(self):
        listing = {"marketplace_categories": {"poshmark": "Men > Shoes"}, "category_path": "Old"}
        choose(listing, requested={"category_path": TEE_PATH}, markets={"poshmark"})
        self.assertEqual(listing["direct_category_ids"]["poshmark"], f"{DEPT}/{SHIRTS}/{TEES}")

    def test_a_general_clothing_path_maps_to_each_native_category(self):
        path = "Clothing, Shoes & Accessories > Men > Men's Clothing > Shirts > T-Shirts"
        listing = {"category_path": path}
        choose(listing, requested={"category_path": path}, catalogs={"poshmark": POSHMARK, "depop": DEPOP, "ebay": EBAY})
        self.assertEqual(listing["direct_category_ids"]["poshmark"], f"{DEPT}/{SHIRTS}/{TEES}")
        self.assertEqual(listing["direct_category_ids"]["depop"], "menswear_tees")
        self.assertEqual(listing["direct_category_ids"]["ebay"], "15687")
        self.assertEqual(listing["direct_category_ids"]["ebay_path"], "Clothing > Men > Shirts > Tees")

    def test_the_assistant_path_wins_over_a_rewritten_saved_path(self):
        listing = {"category_path": "Clothing, Shoes & Accessories > Men > Men's Clothing > Shirts > T-Shirts"}
        choose(listing, requested={"category_path": TEE_PATH}, catalogs={"poshmark": POSHMARK, "depop": DEPOP, "ebay": []})
        self.assertEqual(listing["direct_category_ids"]["poshmark"], f"{DEPT}/{SHIRTS}/{TEES}")
        self.assertEqual(listing["direct_category_ids"]["depop"], "menswear_tees")

    def test_a_group_with_narrower_choices_is_not_stored(self):
        listing = {"direct_category_ids": {"poshmark": f"{DEPT}/{SHOES}"}}
        choose(listing, requested={"category_path": "Men > Shirts"}, markets={"poshmark"})
        self.assertEqual(listing["direct_category_ids"]["poshmark"], f"{DEPT}/{SHOES}")

    def test_none_and_a_category_without_narrower_choices_are_saved(self):
        none_listing = {}
        choose(none_listing, requested={"category_path": "Men > Shirts > None"}, markets={"poshmark"})
        shoes = {}
        choose(shoes, requested={"category_path": "Men > Shoes"}, markets={"poshmark"})
        self.assertEqual(none_listing["direct_category_ids"]["poshmark"], f"{DEPT}/{SHIRTS}")
        self.assertEqual(shoes["direct_category_ids"]["poshmark"], f"{DEPT}/{SHOES}")

    def test_an_explicit_terminal_id_is_kept(self):
        listing = {}
        choose(
            listing,
            requested={"category_path": TEE_PATH, "ids": {"poshmark": f"{DEPT}/{SHOES}"}},
            explicit={"poshmark"},
            catalogs={"poshmark": POSHMARK, "depop": DEPOP, "ebay": []},
        )
        self.assertEqual(listing["direct_category_ids"]["poshmark"], f"{DEPT}/{SHOES}")
        self.assertEqual(listing["direct_category_ids"]["depop"], "menswear_tees")

    def test_an_explicit_id_the_catalog_does_not_list_is_removed(self):
        listing = {"direct_category_ids": {"poshmark": "nope", "ebay": "1"}}
        choose(listing, requested={"ids": {"poshmark": "nope"}}, markets={"poshmark"}, explicit={"poshmark"})
        self.assertNotIn("poshmark", listing["direct_category_ids"])
        self.assertEqual(listing["direct_category_ids"]["ebay"], "1")

    def test_mercari_is_left_unset_until_its_own_leaf_is_known(self):
        named = {}
        choose(named, catalogs={"poshmark": POSHMARK, "depop": DEPOP, "ebay": []})
        self.assertNotIn("mercari", named.get("direct_category_ids", {}))
        matched = {}
        choose(matched, requested={"category_path": "Women > Tops > Tees"}, markets={"mercari"})
        self.assertEqual(matched["direct_category_ids"]["mercari"], "1911")
        kept = {}
        choose(kept, requested={"ids": {"mercari": "1911"}}, markets={"mercari"}, explicit={"mercari"}, catalogs={})
        self.assertEqual(kept["direct_category_ids"]["mercari"], "1911")
        parent = {"direct_category_ids": {"mercari": "1877"}}
        choose(parent, requested={"ids": {"mercari": "1877"}}, markets={"mercari"}, explicit={"mercari"})
        self.assertNotIn("mercari", parent.get("direct_category_ids", {}))

    def test_two_ebay_leaves_with_the_same_name_are_not_guessed(self):
        listing = {}
        choose(
            listing,
            requested={"category_path": "T-Shirts"},
            markets={"ebay"},
            catalogs={"ebay": [
                {"id": "1", "path": "Clothing > Men > T-Shirts"},
                {"id": "2", "path": "Clothing > Women > T-Shirts"},
            ]},
        )
        self.assertNotIn("direct_category_ids", listing)

    def test_changing_category_clears_the_previous_size(self):
        listing = {
            "direct_category_ids": {"poshmark": f"{DEPT}/{SHOES}"},
            "poshmark_specifics": {"subcategory": "Tees", "nativeSizeId": "M", "sellerNotes": "keep"},
            "seller_fields": ["poshmark_specifics.nativeSizeId"],
        }
        choose(listing, markets={"poshmark"})
        self.assertEqual(listing["direct_category_ids"]["poshmark"], f"{DEPT}/{SHIRTS}/{TEES}")
        self.assertNotIn("subcategory", listing["poshmark_specifics"])
        self.assertEqual(listing["poshmark_specifics"]["nativeSizeId"], "M")
        self.assertEqual(listing["poshmark_specifics"]["sellerNotes"], "keep")

    def test_a_locked_category_stays(self):
        listing = {
            "direct_category_ids": {"poshmark": f"{DEPT}/{SHOES}"},
            "seller_fields": ["direct_category_ids.poshmark"],
        }
        written = choose(listing, markets={"poshmark"})
        self.assertEqual(listing["direct_category_ids"]["poshmark"], f"{DEPT}/{SHOES}")
        self.assertEqual(written, [])

    def test_sellwild_uses_its_own_category_name(self):
        listing = {}
        choose(listing, requested={"category_path": "Fashion > Shoes"}, markets={"sellwild"}, catalogs={})
        self.assertEqual(listing["sellwild_specifics"]["nativeCategoryId"], "69")

    def test_a_price_edit_does_not_choose_a_category(self):
        previous = {"category_path": "Men > Shoes", "price": 10, "direct_category_ids": {"poshmark": f"{DEPT}/{SHOES}"}}
        current = {**previous, "price": 12}
        markets, explicit = markets_for_choice(previous, current, requested_choices([
            {"op": "replace", "path": "/price", "value": 12},
        ]))
        self.assertEqual(markets, set())
        self.assertEqual(explicit, set())

    def test_a_category_patch_and_a_direct_id_are_both_read(self):
        requested = requested_choices([
            {"op": "replace", "path": "/category_path", "value": TEE_PATH},
            {"op": "replace", "path": "/direct_category_ids/depop", "value": "menswear_tees"},
        ])
        markets, explicit = markets_for_choice({"category_path": "Old"}, {"category_path": TEE_PATH}, requested)
        self.assertEqual(requested["category_path"], TEE_PATH)
        self.assertEqual(requested["ids"]["depop"], "menswear_tees")
        self.assertEqual(explicit, {"depop"})
        self.assertEqual(markets, set(MARKETS))

    def test_the_chat_prompt_asks_for_the_last_category(self):
        source = inspect.getsource(build_chat_messages) + inspect.getsource(current_listing_prompt)
        self.assertIn("Men > Shirts > Tees", source)
        self.assertIn("direct_category_ids.poshmark", source)
        self.assertIn("sellwild_specifics.nativeCategoryId", source)


class AgentCategorySaveTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.engine = engine
        self.db = sessionmaker(bind=engine)()
        self.conv = ConversationRepo(self.db).create(title="Tee")
        self.repo = ListingRepo(self.db)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _save(self, listing, parent=None):
        return self.repo.save_revision(
            self.conv.id, listing, source="model_refinement", parent_revision_id=parent,
        )

    async def test_a_saved_category_path_writes_the_form_ids(self):
        parent = self._save({"title": "Tee", "category_path": "Old", "direct_category_ids": {"poshmark": "old/id"},
                             "poshmark_specifics": {"subcategory": "Tees", "nativeSizeId": "M"}})
        self._save({
            "title": "Tee",
            "category_path": "Clothing, Shoes & Accessories > Men > Men's Clothing > Shirts > T-Shirts",
            "direct_category_ids": {"poshmark": "old/id"},
            "poshmark_specifics": {"subcategory": "Tees", "nativeSizeId": "M"},
        }, parent.id)
        self.db.add(CategoryTree(marketplace="ebay", status="complete", roots_loaded=True))
        self.db.add(CategoryTreeNode(
            marketplace="ebay", category_id="15687", parent_id="0", path="Men > Shirts > Tees",
            label="Tees", is_leaf=True, has_children=False, children_loaded=True,
        ))
        self.db.commit()
        taxonomy = AsyncMock(side_effect=AssertionError("taxonomy"))
        with patch("vendoo_studio.services.chrome_catalog.ensure_catalogs", new=AsyncMock(return_value={
            "poshmark": POSHMARK, "depop": DEPOP,
        })), patch("vendoo_studio.services.direct_categories._ebay_leaf", new=taxonomy):
            await apply_saved_agent_category(self.db, self.conv.id, operations=[
                {"op": "replace", "path": "/category_path", "value": TEE_PATH},
            ], source_text="")
        taxonomy.assert_not_called()
        saved = self.repo.get_revisions(self.conv.id)[0].listing_json
        self.assertEqual(saved["direct_category_ids"]["poshmark"], f"{DEPT}/{SHIRTS}/{TEES}")
        self.assertEqual(saved["direct_category_ids"]["depop"], "menswear_tees")
        self.assertEqual(saved["direct_category_ids"]["ebay"], "15687")
        self.assertEqual(saved["direct_category_ids"]["ebay_path"], "Men > Shirts > Tees")
        self.assertEqual(saved["sellwild_specifics"]["nativeCategoryId"], "270")
        self.assertNotIn("subcategory", saved["poshmark_specifics"])
        self.assertNotIn("nativeSizeId", saved["poshmark_specifics"])
        messages = [message.text for message in ConversationRepo(self.db).get_messages(self.conv.id)]
        self.assertTrue(any(text.startswith("Saved the category for ") and "Poshmark" in text and "eBay" in text for text in messages))

    async def test_ebay_suggestions_fill_a_category_the_synced_tree_does_not_have(self):
        parent = self._save({"title": "Tee", "category_path": "Old"})
        self._save({"title": "Tee", "category_path": "Old"}, parent.id)
        taxonomy = AsyncMock(return_value={"id": "15687", "path": "Clothing > Men > Shirts > Tees"})
        with patch("vendoo_studio.services.chrome_catalog.ensure_catalogs", new=AsyncMock(return_value={})), \
                patch("vendoo_studio.services.direct_categories._ebay_leaf", new=taxonomy):
            await apply_saved_agent_category(self.db, self.conv.id, operations=[
                {"op": "replace", "path": "/category_path", "value": TEE_PATH},
            ])
        saved = self.repo.get_revisions(self.conv.id)[0].listing_json
        self.assertEqual(saved["direct_category_ids"]["ebay"], "15687")
        self.assertEqual(saved["direct_category_ids"]["ebay_path"], "Clothing > Men > Shirts > Tees")
        taxonomy.assert_awaited()

    async def test_a_price_save_does_not_load_catalogs(self):
        parent = self._save({"title": "Tee", "category_path": "Men > Shoes", "price": 10})
        self._save({"title": "Tee", "category_path": "Men > Shoes", "price": 12}, parent.id)
        catalogs = AsyncMock(return_value={})
        with patch("vendoo_studio.services.chrome_catalog.ensure_catalogs", new=catalogs):
            await apply_saved_agent_category(self.db, self.conv.id, operations=[
                {"op": "replace", "path": "/price", "value": 12},
            ])
        catalogs.assert_not_called()
        self.assertEqual(len(self.repo.get_revisions(self.conv.id)), 2)


class GeneralCategoryMapTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.engine = engine
        self.db = sessionmaker(bind=engine)()
        self.db.add(CategoryTree(marketplace="ebay", status="complete", roots_loaded=True))
        self.db.add(CategoryTreeNode(
            marketplace="ebay", category_id="15687", parent_id="0", path="Clothing > Men > Shirts > Tees",
            label="Tees", is_leaf=True, has_children=False, children_loaded=True,
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_saving_the_general_category_fills_each_form(self):
        previous = {"title": "Tee", "category_path": "Old", "direct_category_ids": {"poshmark": "old/id"},
                    "poshmark_specifics": {"subcategory": "Tees", "nativeSizeId": "M"}}
        listing = {
            "title": "Tee",
            "category_path": "Clothing, Shoes & Accessories > Men > Men's Clothing > Shirts > T-Shirts",
            "direct_category_ids": {"poshmark": "old/id"},
            "poshmark_specifics": {"subcategory": "Tees", "nativeSizeId": "M"},
        }
        taxonomy = AsyncMock(side_effect=AssertionError("taxonomy"))
        with patch("vendoo_studio.services.chrome_catalog.ensure_catalogs", new=AsyncMock(return_value={
            "poshmark": POSHMARK, "depop": DEPOP,
        })), patch("vendoo_studio.services.direct_categories._ebay_leaf", new=taxonomy):
            map_general_category(self.db, previous, listing)
        taxonomy.assert_not_called()
        self.assertEqual(listing["direct_category_ids"]["poshmark"], f"{DEPT}/{SHIRTS}/{TEES}")
        self.assertEqual(listing["direct_category_ids"]["depop"], "menswear_tees")
        self.assertEqual(listing["direct_category_ids"]["ebay"], "15687")
        self.assertEqual(listing["direct_category_ids"]["ebay_path"], "Clothing > Men > Shirts > Tees")
        self.assertEqual(listing["sellwild_specifics"]["nativeCategoryId"], "270")
        self.assertNotIn("subcategory", listing["poshmark_specifics"])
        self.assertNotIn("nativeSizeId", listing["poshmark_specifics"])

    def test_a_category_picked_on_its_own_form_stays(self):
        previous = {"category_path": "Old", "direct_category_ids": {"poshmark": "old/id"}}
        listing = {
            "category_path": TEE_PATH,
            "direct_category_ids": {"poshmark": f"{DEPT}/{SHOES}"},
        }
        with patch("vendoo_studio.services.chrome_catalog.ensure_catalogs", new=AsyncMock(return_value={
            "poshmark": POSHMARK, "depop": DEPOP,
        })), patch("vendoo_studio.services.direct_categories._ebay_leaf", new=AsyncMock(return_value=None)):
            map_general_category(self.db, previous, listing)
        self.assertEqual(listing["direct_category_ids"]["poshmark"], f"{DEPT}/{SHOES}")
        self.assertEqual(listing["direct_category_ids"]["depop"], "menswear_tees")

    def test_a_price_edit_leaves_the_marketplace_categories(self):
        listing = {"category_path": "Men > Shoes", "price": 12, "direct_category_ids": {"poshmark": f"{DEPT}/{SHOES}"}}
        catalogs = AsyncMock(return_value={})
        with patch("vendoo_studio.services.chrome_catalog.ensure_catalogs", new=catalogs):
            map_general_category(self.db, {"category_path": "Men > Shoes", "price": 10, "direct_category_ids": {"poshmark": f"{DEPT}/{SHOES}"}}, listing)
        catalogs.assert_not_called()
        self.assertEqual(listing["direct_category_ids"]["poshmark"], f"{DEPT}/{SHOES}")
