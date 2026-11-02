"""Each direct form's category comes from that form's own options, never a stale one."""

from __future__ import annotations

import inspect
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from vendoo_studio.services import listing_persistence
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

GENERAL_TEE = "Clothing, Shoes & Accessories > Men > Men's Clothing > Shirts > T-Shirts"
GENERAL_GAMES = "Video Games & Consoles > Video Games"
SHIRTS = "dept/shirts"
SHORT = "dept/shirts/short"
SHOES = "dept/shoes/sneakers"
POSHMARK = [
    {"id": SHORT, "path": "Men > Shirts > Tees - Short Sleeve"},
    {"id": "dept/shirts/long", "path": "Men > Shirts > Tees - Long Sleeve"},
    {"id": SHIRTS, "path": "Men > Shirts > None"},
    {"id": SHOES, "path": "Men > Shoes > Sneakers"},
]
DEPOP = [{"id": "menswear_tshirts", "path": "Men > Tops > T-shirts"}, {"id": "menswear_sneakers", "path": "Men > Footwear > Sneakers"}]
EBAY = [{"id": "15687", "path": GENERAL_TEE}, {"id": "15709", "path": "Clothing, Shoes & Accessories > Men > Men's Shoes > Athletic Shoes"}]
MERCARI = [{"id": "303", "path": "Men > Tops > T-shirts"}, {"id": "1911", "path": "Women > Tops & blouses > Tees"}]
SELLWILD = [{"id": "270", "path": "Fashion > Tops & Shirts"}, {"id": "69", "path": "Fashion > Shoes"}]
CATALOGS = {"poshmark": POSHMARK, "depop": DEPOP, "ebay": EBAY, "mercari": MERCARI, "sellwild": SELLWILD}


def choose(listing, *, requested=None, markets=None, explicit=None, catalogs=None):
    return apply_agent_categories(
        listing,
        CATALOGS if catalogs is None else catalogs,
        requested=requested if requested is not None else {"category_path": GENERAL_TEE},
        markets=set(MARKETS) if markets is None else markets,
        explicit=explicit or set(),
    )


class AgentCategoryChoiceTest(unittest.TestCase):
    def test_a_general_category_sets_each_forms_own_option(self):
        listing = {"category_path": GENERAL_TEE}
        written = choose(listing)
        self.assertEqual(listing["direct_category_ids"]["poshmark"], SHIRTS)
        self.assertEqual(listing["direct_category_ids"]["depop"], "menswear_tshirts")
        self.assertEqual(listing["direct_category_ids"]["mercari"], "303")
        self.assertEqual(listing["direct_category_ids"]["ebay"], "15687")
        self.assertEqual(listing["direct_category_ids"]["ebay_path"], GENERAL_TEE)
        self.assertEqual(listing["sellwild_specifics"]["nativeCategoryId"], "270")
        self.assertIn("direct_category_ids.poshmark", written)
        self.assertIn("sellwild_specifics.nativeCategoryId", written)

    def test_the_listings_own_words_choose_between_qualified_options(self):
        listing = {"title": "Nike Graphic Tee", "ebay_specifics": {"sleeveLength": "Short Sleeve"}}
        choose(listing, markets={"poshmark"})
        self.assertEqual(listing["direct_category_ids"]["poshmark"], SHORT)

    def test_a_category_no_option_matches_clears_the_old_one(self):
        listing = {
            "direct_category_ids": {"poshmark": SHORT, "depop": "menswear_tshirts"},
            "poshmark_specifics": {"subcategory": "Tees", "nativeSizeId": "M"},
            "sellwild_specifics": {"nativeCategoryId": "270"},
        }
        written = choose(listing, requested={"category_path": GENERAL_GAMES})
        self.assertNotIn("direct_category_ids", listing)
        self.assertNotIn("sellwild_specifics", listing)
        self.assertNotIn("subcategory", listing["poshmark_specifics"])
        self.assertIn("direct_category_ids.poshmark", written)

    def test_an_explicit_option_is_kept_and_an_unknown_one_removed(self):
        listing = {}
        choose(listing, requested={"category_path": GENERAL_TEE, "ids": {"poshmark": SHOES}},
               markets={"poshmark", "depop"}, explicit={"poshmark"})
        self.assertEqual(listing["direct_category_ids"]["poshmark"], SHOES)
        self.assertEqual(listing["direct_category_ids"]["depop"], "menswear_tshirts")
        unknown = {"direct_category_ids": {"poshmark": "nope", "ebay": "1"}}
        choose(unknown, requested={"ids": {"poshmark": "nope"}}, markets={"poshmark"}, explicit={"poshmark"})
        self.assertNotIn("poshmark", unknown["direct_category_ids"])
        self.assertEqual(unknown["direct_category_ids"]["ebay"], "1")

    def test_an_explicit_path_saves_that_options_id(self):
        listing = {}
        choose(listing, requested={"ids": {"depop": "Men > Footwear > Sneakers"}}, markets={"depop"}, explicit={"depop"})
        self.assertEqual(listing["direct_category_ids"]["depop"], "menswear_sneakers")

    def test_a_mercari_parent_is_not_an_option(self):
        parent = {"direct_category_ids": {"mercari": "1877"}}
        choose(parent, requested={"ids": {"mercari": "1877"}}, markets={"mercari"}, explicit={"mercari"})
        self.assertNotIn("direct_category_ids", parent)

    def test_two_equal_options_are_not_guessed(self):
        listing = {}
        choose(listing, requested={"category_path": "T-Shirts"}, markets={"ebay"}, catalogs={"ebay": [
            {"id": "1", "path": "Clothing > Men > T-Shirts"},
            {"id": "2", "path": "Clothing > Women > T-Shirts"},
        ]})
        self.assertNotIn("direct_category_ids", listing)

    def test_changing_category_clears_the_previous_size_unless_locked(self):
        listing = {
            "direct_category_ids": {"poshmark": SHOES},
            "poshmark_specifics": {"subcategory": "Tees", "nativeSizeId": "M", "sellerNotes": "keep"},
            "seller_fields": ["poshmark_specifics.nativeSizeId"],
        }
        choose(listing, markets={"poshmark"})
        self.assertEqual(listing["direct_category_ids"]["poshmark"], SHIRTS)
        self.assertNotIn("subcategory", listing["poshmark_specifics"])
        self.assertEqual(listing["poshmark_specifics"]["nativeSizeId"], "M")
        self.assertEqual(listing["poshmark_specifics"]["sellerNotes"], "keep")

    def test_a_locked_category_stays(self):
        listing = {"direct_category_ids": {"poshmark": SHOES}, "seller_fields": ["direct_category_ids.poshmark"]}
        written = choose(listing, requested={"category_path": GENERAL_GAMES}, markets={"poshmark"})
        self.assertEqual(listing["direct_category_ids"]["poshmark"], SHOES)
        self.assertEqual(written, [])

    def test_a_price_edit_does_not_choose_a_category(self):
        previous = {"category_path": "Men > Shoes", "price": 10, "direct_category_ids": {"poshmark": SHOES}}
        current = {**previous, "price": 12}
        markets, explicit = markets_for_choice(previous, current, requested_choices([
            {"op": "replace", "path": "/price", "value": 12},
        ]))
        self.assertEqual(markets, set())
        self.assertEqual(explicit, set())

    def test_a_category_patch_and_a_direct_id_are_both_read(self):
        requested = requested_choices([
            {"op": "replace", "path": "/category_path", "value": GENERAL_TEE},
            {"op": "replace", "path": "/direct_category_ids/depop", "value": "menswear_tshirts"},
        ])
        markets, explicit = markets_for_choice({"category_path": "Old"}, {"category_path": GENERAL_TEE}, requested)
        self.assertEqual(requested["category_path"], GENERAL_TEE)
        self.assertEqual(requested["ids"]["depop"], "menswear_tshirts")
        self.assertEqual(explicit, {"depop"})
        self.assertEqual(markets, set(MARKETS))

    def test_the_chat_prompt_asks_for_the_last_category(self):
        source = inspect.getsource(build_chat_messages) + inspect.getsource(current_listing_prompt)
        self.assertIn("Men > Shirts > Tees", source)
        self.assertIn("direct_category_ids.poshmark", source)
        self.assertIn("sellwild_specifics.nativeCategoryId", source)


def _database():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(CategoryTree(marketplace="ebay", status="complete", roots_loaded=True))
    db.add(CategoryTreeNode(
        marketplace="ebay", category_id="15687", parent_id="0", path=GENERAL_TEE,
        label="T-Shirts", is_leaf=True, has_children=False, children_loaded=True,
    ))
    db.commit()
    return engine, db


def _no_network():
    return (
        patch("vendoo_studio.services.direct_categories._ebay_leaf", new=AsyncMock(side_effect=AssertionError("eBay"))),
        patch("vendoo_studio.services.direct_categories._etsy_leaf",
              new=AsyncMock(return_value={"id": "482", "path": "Clothing > Men's Clothing > Shirts & Tees > T-shirts"})),
    )


class AgentCategorySaveTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine, self.db = _database()
        self.conv = ConversationRepo(self.db).create(title="Tee")
        self.repo = ListingRepo(self.db)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _save(self, listing, parent=None):
        return listing_persistence.save_revision(self.repo.db, self.conv.id, listing, source="model_refinement", parent_revision_id=parent)

    def _messages(self):
        return [message.text for message in ConversationRepo(self.db).get_messages(self.conv.id)]

    async def test_a_chat_category_writes_each_forms_own_option(self):
        parent = self._save({"title": "Tee", "category_path": "Old", "direct_category_ids": {"poshmark": "old/id"},
                             "poshmark_specifics": {"subcategory": "Tees", "nativeSizeId": "M"}})
        self._save({"title": "Tee", "category_path": GENERAL_TEE, "direct_category_ids": {"poshmark": "old/id"},
                    "poshmark_specifics": {"subcategory": "Tees", "nativeSizeId": "M"}}, parent.id)
        ebay, etsy = _no_network()
        with ebay, etsy:
            await apply_saved_agent_category(self.db, self.conv.id, operations=[
                {"op": "replace", "path": "/category_path", "value": GENERAL_TEE},
            ])
        saved = self.repo.get_revisions(self.conv.id)[0].listing_json
        self.assertEqual(saved["direct_category_ids"]["poshmark"], "01008c10d97b4e1245005764/07008c10d97b4e1245005764")
        self.assertEqual(saved["direct_category_ids"]["depop"], "menswear_tshirts")
        self.assertEqual(saved["direct_category_ids"]["mercari"], "303")
        self.assertEqual(saved["direct_category_ids"]["ebay"], "15687")
        self.assertEqual(saved["direct_category_ids"]["etsy"], "482")
        self.assertEqual(saved["sellwild_specifics"]["nativeCategoryId"], "270")
        self.assertEqual(saved["vinted_specifics"]["category"], "Men > Clothing > Tops & t-shirts > T-shirts")
        self.assertNotIn("subcategory", saved["poshmark_specifics"])
        self.assertTrue(any(text.startswith("Saved the category for eBay, Poshmark, Mercari, Depop, Sellwild, Etsy, and Vinted")
                            for text in self._messages()), self._messages())

    async def test_a_market_without_a_match_is_cleared_and_named(self):
        parent = self._save({"title": "Mario Kart", "category_path": GENERAL_TEE,
                             "direct_category_ids": {"depop": "menswear_tshirts", "poshmark": "old/id"}})
        self._save({"title": "Mario Kart", "category_path": GENERAL_GAMES,
                    "direct_category_ids": {"depop": "menswear_tshirts", "poshmark": "old/id"}}, parent.id)
        etsy = AsyncMock(return_value=None)
        with patch("vendoo_studio.services.direct_categories._ebay_leaf", new=AsyncMock(return_value=None)), \
                patch("vendoo_studio.services.direct_categories._etsy_leaf", new=etsy):
            await apply_saved_agent_category(self.db, self.conv.id, operations=[
                {"op": "replace", "path": "/category_path", "value": GENERAL_GAMES},
            ])
        saved = self.repo.get_revisions(self.conv.id)[0].listing_json
        self.assertNotIn("depop", saved["direct_category_ids"])
        self.assertTrue(saved["direct_category_ids"]["poshmark"].count("/") == 2)
        self.assertTrue(any("Pick the category on Depop" in text for text in self._messages()), self._messages())

    async def test_ebay_suggestions_fill_a_category_the_synced_tree_does_not_have(self):
        parent = self._save({"title": "Boots", "category_path": "Old"})
        self._save({"title": "Boots", "category_path": "Old"}, parent.id)
        suggestion = AsyncMock(return_value={"id": "11498", "path": "Men > Men's Shoes > Boots"})
        with patch("vendoo_studio.services.direct_categories._ebay_leaf", new=suggestion), \
                patch("vendoo_studio.services.direct_categories._etsy_leaf", new=AsyncMock(return_value=None)):
            await apply_saved_agent_category(self.db, self.conv.id, operations=[
                {"op": "replace", "path": "/category_path", "value": "Clothing, Shoes & Accessories > Men > Men's Shoes > Boots"},
            ])
        saved = self.repo.get_revisions(self.conv.id)[0].listing_json
        self.assertEqual(saved["direct_category_ids"]["ebay"], "11498")
        self.assertEqual(saved["direct_category_ids"]["ebay_path"], "Men > Men's Shoes > Boots")
        suggestion.assert_awaited()

    async def test_a_price_save_does_not_choose_categories(self):
        parent = self._save({"title": "Tee", "category_path": "Men > Shoes", "price": 10})
        self._save({"title": "Tee", "category_path": "Men > Shoes", "price": 12}, parent.id)
        with patch("vendoo_studio.services.agent_categories.native_rows", side_effect=AssertionError("rows")):
            await apply_saved_agent_category(self.db, self.conv.id, operations=[
                {"op": "replace", "path": "/price", "value": 12},
            ])
        self.assertEqual(len(self.repo.get_revisions(self.conv.id)), 2)


class GeneralCategoryMapTest(unittest.TestCase):
    def setUp(self):
        self.engine, self.db = _database()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_saving_the_general_category_fills_each_form(self):
        previous = {"title": "Tee", "category_path": "Old", "direct_category_ids": {"poshmark": "old/id"},
                    "poshmark_specifics": {"subcategory": "Tees", "nativeSizeId": "M"}}
        listing = {**previous, "category_path": GENERAL_TEE, "ebay_specifics": {"sleeveLength": "Short Sleeve"}}
        ebay, etsy = _no_network()
        with ebay, etsy:
            map_general_category(self.db, previous, listing)
        self.assertEqual(listing["direct_category_ids"]["poshmark"],
                         "01008c10d97b4e1245005764/07008c10d97b4e1245005764/33009813d97b4e3995005764")
        self.assertEqual(listing["direct_category_ids"]["depop"], "menswear_tshirts")
        self.assertEqual(listing["direct_category_ids"]["ebay"], "15687")
        self.assertEqual(listing["sellwild_specifics"]["nativeCategoryId"], "270")
        self.assertNotIn("subcategory", listing["poshmark_specifics"])

    def test_it_also_maps_when_called_inside_a_running_loop(self):
        import asyncio

        previous = {"title": "Tee", "category_path": "Old"}
        listing = {"title": "Tee", "category_path": GENERAL_TEE}
        ebay, etsy = _no_network()

        async def inside():
            map_general_category(self.db, previous, listing)

        with ebay, etsy:
            asyncio.run(inside())
        self.assertEqual(listing["direct_category_ids"]["depop"], "menswear_tshirts")

    def test_a_category_picked_on_its_own_form_stays(self):
        previous = {"category_path": "Old", "direct_category_ids": {"poshmark": "old/id"}}
        mine = "01008c10d97b4e1245005764/07008c10d97b4e1245005764/33009813d97b4e3995005764"
        listing = {"category_path": GENERAL_GAMES, "direct_category_ids": {"poshmark": mine}}
        with patch("vendoo_studio.services.direct_categories._ebay_leaf", new=AsyncMock(return_value=None)), \
                patch("vendoo_studio.services.direct_categories._etsy_leaf", new=AsyncMock(return_value=None)):
            map_general_category(self.db, previous, listing)
        self.assertEqual(listing["direct_category_ids"]["poshmark"], mine)

    def test_a_price_edit_leaves_the_marketplace_categories(self):
        previous = {"category_path": "Men > Shoes", "price": 10, "direct_category_ids": {"poshmark": SHOES}}
        listing = {**previous, "price": 12}
        with patch("vendoo_studio.services.agent_categories.native_rows", side_effect=AssertionError("rows")):
            map_general_category(self.db, previous, listing)
        self.assertEqual(listing["direct_category_ids"]["poshmark"], SHOES)
