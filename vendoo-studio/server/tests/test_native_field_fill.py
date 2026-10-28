"""The listing agent fills each marketplace's category fields from the form its tab shows."""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from vendoo_studio.database import Base
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services import native_field_fill
from vendoo_studio.services.native_field_fill import fill_native_fields, fillable_rows, validated_values, write_value


def row(path, label, options, *, value=None, multi=False, max_values=None, option_values=None, **extra):
    return {"key": path, "path": path, "label": label, "value": [] if value is None and multi else (value or ""),
            "required": False, "multi": multi, "selection_only": True, "options": options,
            "option_values": option_values or {}, "max_values": max_values, **extra}


DEPOP = {"marketplace": "depop", "native": True, "known": True, "category_id": "menswear_jeans", "fields": [
    row("depop_specifics.description", "Description", []),
    row("price", "Item price", []),
    row("depop_specifics.attributes.bottom-fit", "Type", ["Ripped", "Faded", "Acid-washed"], multi=True, max_values=2,
        option_values={"Ripped": "ripped", "Faded": "faded", "Acid-washed": "acid-washed"}, category_specific=True),
    row("depop_specifics.attributes.bottom-style", "Fit", ["Slim", "Straight leg"], multi=True, max_values=2,
        option_values={"Slim": "slim", "Straight leg": "straight-leg"}, category_specific=True),
    row("depop_specifics.attributes.body-fit", "Body fit", ["Tall"], multi=True, max_values=2, value=["tall"],
        option_values={"Tall": "tall"}, category_specific=True),
    row("depop_specifics.brand", "Brand", ["Levi's", "Other"]),
    row("depop_specifics.parcelSize", "Package size", ["Medium"]),
    row("depop_specifics.age", "Age", ["Modern", "90s"], multi=True, max_values=1,
        option_values={"Modern": "modern", "90s": "90s"}),
]}
EBAY = {"marketplace": "ebay", "native": True, "known": True, "category_id": "11483", "fields": [
    row("ebay_specifics.Rise", "Rise", ["Low", "Mid", "High"], category_specific=True),
    row("ebay_specifics.Closure", "Closure", ["Button", "Zip"], category_specific=True),
    row("ebay_specifics.Brand", "Brand", ["Levi's"], category_specific=True),
]}


class FillableRowsTest(unittest.TestCase):
    def test_only_empty_item_choices_the_seller_has_not_set(self):
        listing = {"seller_fields": ["depop_specifics.attributes.bottom-style"]}
        paths = [r["path"] for r in fillable_rows(DEPOP, "depop", listing)]
        # Filled (Body fit), locked (Fit), sale rows (price, package) and brand are left alone.
        self.assertEqual(paths, ["depop_specifics.attributes.bottom-fit", "depop_specifics.age"])

    def test_a_row_locked_by_the_form_or_with_too_many_choices_is_skipped(self):
        form = {"fields": [row("ebay_specifics.Color", "Color", [str(n) for n in range(400)]),
                           row("ebay_specifics.Style", "Style", ["Jeans"], locked=True),
                           row("ebay_specifics.donationPercent", "Percentage", ["10%", "100%"]),
                           row("ebay_specifics.Fit", "Fit", ["Slim"])]}
        self.assertEqual([r["path"] for r in fillable_rows(form, "ebay", {})], ["ebay_specifics.Fit"])


class ValidatedValuesTest(unittest.TestCase):
    rows = {r["path"]: r for r in DEPOP["fields"] + EBAY["fields"]}

    def test_keeps_listed_choices_as_their_ids_and_drops_the_rest(self):
        reply = {"fields": {
            "depop_specifics.attributes.bottom-fit": ["ripped", "Faded", "Bleached", "Acid-washed"],
            "depop_specifics.age": ["90s", "Modern"],
            "ebay_specifics.Rise": "mid",
            "ebay_specifics.Closure": "Velcro",
            "ebay_specifics.Unknown": "x",
        }}
        accepted = validated_values(reply, self.rows)
        self.assertEqual(accepted["depop_specifics.attributes.bottom-fit"], (["ripped", "faded"], ["Ripped", "Faded"]))
        self.assertEqual(accepted["depop_specifics.age"], (["90s"], ["90s"]))
        self.assertEqual(accepted["ebay_specifics.Rise"], ("Mid", ["Mid"]))
        self.assertNotIn("ebay_specifics.Closure", accepted)
        self.assertNotIn("ebay_specifics.Unknown", accepted)


class WriteValueTest(unittest.TestCase):
    def test_writes_like_the_editor(self):
        listing = {"ebay_specifics": {"rise": "Low"}, "depop_specifics": {"attributes": {"material": ["denim"]}}}
        write_value(listing, "ebay_specifics.Rise", "Mid")
        write_value(listing, "depop_specifics.attributes.bottom-fit", ["ripped"])
        self.assertEqual(listing["ebay_specifics"], {"Rise": "Mid"})
        self.assertEqual(listing["depop_specifics"]["attributes"], {"material": ["denim"], "bottom-fit": ["ripped"]})


class FillNativeFieldsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.conv = ConversationRepo(self.db).create(title="Jeans")
        self.repo = ListingRepo(self.db)
        self.repo.save_revision(self.conv.id, {"title": "Levi's 505 jeans", "brand": "Levi's"}, source="model")

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _current(self):
        saved = self.repo.get_current(self.conv.id)
        return self.repo.get_revision(saved.current_revision_id)

    async def test_saves_valid_picks_and_reports_them(self):
        reply = json.dumps({"fields": {"depop_specifics.attributes.bottom-fit": ["Ripped", "Torn"], "ebay_specifics.Rise": "High"}})
        with patch.object(native_field_fill, "load_forms", AsyncMock(return_value={"depop": DEPOP, "ebay": EBAY})), \
                patch("vendoo_studio.services.listing_generate.collect_provider_text", AsyncMock(return_value=reply)) as ask:
            updated = await fill_native_fields(self.db, self.conv.id, object(), evidence="Ripped knees, high rise")
        prompt = ask.call_args.args[1][1]["content"]
        self.assertIn("depop_specifics.attributes.bottom-fit | Type | pick up to 2 | choices: Ripped; Faded; Acid-washed", prompt)
        self.assertNotIn("Package size", prompt)
        self.assertEqual(updated["depop_specifics"]["attributes"]["bottom-fit"], ["ripped"])
        self.assertEqual(updated["ebay_specifics"]["Rise"], "High")
        current = self._current()
        self.assertEqual(current.source, "native_fields")
        self.assertEqual(current.listing_json["ebay_specifics"]["Rise"], "High")
        messages = [m.text for m in ConversationRepo(self.db).get_messages(self.conv.id)]
        self.assertTrue(any("Depop: Type: Ripped" in text and "eBay: Rise: High" in text for text in messages))

    async def test_nothing_is_saved_without_a_form_or_a_valid_pick(self):
        before = self._current().id
        ask = AsyncMock(return_value=json.dumps({"fields": {"ebay_specifics.Rise": "Sky high"}}))
        with patch("vendoo_studio.services.listing_generate.collect_provider_text", ask):
            with patch.object(native_field_fill, "load_forms", AsyncMock(return_value={})):
                self.assertIsNone(await fill_native_fields(self.db, self.conv.id, object(), evidence=""))
            ask.assert_not_called()
            with patch.object(native_field_fill, "load_forms", AsyncMock(return_value={"ebay": EBAY})):
                self.assertIsNone(await fill_native_fields(self.db, self.conv.id, object(), evidence=""))
        self.assertEqual(self._current().id, before)

    async def test_a_seller_edit_during_the_fill_wins(self):
        async def edit_then_reply(*_args, **_kwargs):
            self.repo.save_revision(self.conv.id, {"title": "Seller's title"}, source="editor")
            return json.dumps({"fields": {"ebay_specifics.Rise": "High"}})

        with patch.object(native_field_fill, "load_forms", AsyncMock(return_value={"ebay": EBAY})), \
                patch("vendoo_studio.services.listing_generate.collect_provider_text", edit_then_reply):
            self.assertIsNone(await fill_native_fields(self.db, self.conv.id, object(), evidence=""))
        self.assertEqual(self._current().listing_json, {"title": "Seller's title"})


class CategoryChangeTest(unittest.IsolatedAsyncioTestCase):
    """A category the seller picks later gets the same fill, for that marketplace only."""

    async def asyncSetUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.conv = ConversationRepo(self.db).create(title="Jeans")
        self.repo = ListingRepo(self.db)
        self.base = {"title": "Levi's jeans", "direct_category_ids": {"depop": "menswear_jeans", "ebay": "11483"}}
        self.repo.save_revision(self.conv.id, self.base, source="model")
        self.fills: list[set[str]] = []

        async def fake_fill(conv_id, markets):
            self.fills.append(set(markets))

        patches = [
            patch.object(native_field_fill, "SETTLE_SECONDS", 0.05),
            patch.object(native_field_fill, "_fill_after_category", fake_fill),
            patch.object(native_field_fill, "_loop", asyncio.get_running_loop()),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    async def asyncTearDown(self):
        self.db.close()
        self.engine.dispose()

    def save(self, change: dict, source="user_form"):
        listing = json.loads(json.dumps(self.base))
        listing["direct_category_ids"].update(change)
        self.repo.save_revision(self.conv.id, listing, source=source)
        self.base = listing

    async def settle(self):
        await asyncio.sleep(0.3)

    def test_changed_categories_names_only_new_picks(self):
        before = {"direct_category_ids": {"depop": "a", "ebay": "1"}, "sellwild_specifics": {"nativeCategoryId": "5"}}
        after = {"direct_category_ids": {"depop": "b", "ebay": "1", "mercari": ""}, "sellwild_specifics": {"nativeCategoryId": "6"}}
        self.assertEqual(native_field_fill.changed_categories(before, after), {"depop", "sellwild"})

    async def test_a_new_category_fills_that_marketplace_only(self):
        self.save({"depop": "menswear_trousers"})
        await self.settle()
        self.assertEqual(self.fills, [{"depop"}])

    async def test_no_fill_when_the_category_is_unchanged(self):
        listing = {**self.base, "title": "Levi's 505 jeans"}
        self.repo.save_revision(self.conv.id, listing, source="user_form")
        await self.settle()
        self.assertEqual(self.fills, [])

    async def test_its_own_revision_and_generation_do_not_start_a_fill(self):
        self.save({"depop": "menswear_trousers"}, source="native_fields")
        self.save({"ebay": "57989"}, source="generation_finalize")
        await self.settle()
        self.assertEqual(self.fills, [])

    async def test_saves_close_together_are_one_fill(self):
        # Chat saves its changes and then its category; a seller may click through categories.
        self.save({"depop": "menswear_trousers"}, source="model_refinement")
        self.save({"ebay": "57989"}, source="model_refinement")
        self.save({"depop": "menswear_shorts"})
        await self.settle()
        self.assertEqual(self.fills, [{"depop", "ebay"}])


class CategoryFillLocksTest(unittest.IsolatedAsyncioTestCase):
    async def test_only_the_changed_marketplace_form_is_read_and_locks_hold(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        conv = ConversationRepo(db).create(title="Jeans")
        ListingRepo(db).save_revision(conv.id, {"title": "Levi's", "seller_fields": ["depop_specifics.attributes.bottom-fit"]},
                                      source="model")
        reply = json.dumps({"fields": {"depop_specifics.attributes.bottom-fit": ["Ripped"],
                                       "depop_specifics.age": ["90s"], "ebay_specifics.Rise": "High"}})
        loaded = AsyncMock(return_value={"depop": DEPOP})
        with patch.object(native_field_fill, "load_forms", loaded), \
                patch("vendoo_studio.services.listing_generate.collect_provider_text", AsyncMock(return_value=reply)):
            updated = await fill_native_fields(db, conv.id, object(), evidence="", markets={"depop"})
        self.assertEqual(loaded.call_args.args[2], {"depop"})
        self.assertNotIn("bottom-fit", updated.get("depop_specifics", {}).get("attributes", {}))
        self.assertEqual(updated["depop_specifics"]["age"], ["90s"])
        self.assertNotIn("ebay_specifics", updated)
        db.close()
        engine.dispose()
