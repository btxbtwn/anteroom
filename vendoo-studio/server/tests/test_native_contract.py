from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from vendoo_studio.database import Base
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services import native_contract as contract
from vendoo_studio.services.native_field_fill import validated_values


def seed():
    listing = {"title": "Beige pants", "category_path": "Clothing > Women > Pants > Dress pants",
               "direct_category_ids": {"mercari": "12"}, "mercari_specifics": {}}
    form = {"category_id": "12", "fields": [
        {"path": "mercari_specifics.material", "label": "Material", "required": True,
         "selection_only": True, "options": ["Cotton", "Wool"], "option_values": {"Cotton": "cotton", "Wool": "wool"},
         "category_specific": True},
        {"path": "mercari_specifics.notes", "label": "Notes", "max_length": 10},
        {"path": "mercari_specifics.quantity", "label": "Quantity", "input_type": "number", "min_value": 1, "max_value": 5},
    ]}
    listing[contract.KEY] = contract.capture(listing, {"mercari": form}, ["mercari"])
    return listing


class NativeContractTest(unittest.TestCase):
    def test_generated_unknown_fields_and_invalid_choices_do_not_survive(self):
        original = seed()
        generated = {**copy.deepcopy(original), "direct_category_ids": {"mercari": "invented"},
                     "mercari_specifics": {"material": "Leather", "inventedField": "yes", "notes": "too long for notes"}}
        contract.constrain_generated(generated, original)
        self.assertEqual(generated["direct_category_ids"], {"mercari": "12"})
        self.assertEqual(generated["mercari_specifics"], {})
        self.assertTrue(any(item["field"] == "mercari_specifics.material" for item in contract.gaps(generated, "mercari")))

    def test_a_valid_id_and_bounded_number_survive(self):
        listing = seed()
        listing["mercari_specifics"] = {"material": "cotton", "quantity": 2}
        listing[contract.EVIDENCE_KEY] = {"mercari_specifics.material": {"value": "cotton", "quote": "100% cotton"}}
        self.assertEqual(contract.gaps(listing, "mercari"), [])
        listing["mercari_specifics"]["quantity"] = "NaN"
        self.assertTrue(any("finite" in item["message"] for item in contract.gaps(listing, "mercari")))

    def test_resolving_vendoo_ids_does_not_invalidate_an_unchanged_native_category(self):
        listing = seed()
        listing["mercari_specifics"]["material"] = "cotton"
        listing[contract.EVIDENCE_KEY] = {"mercari_specifics.material": {"value": "cotton", "quote": "100% cotton"}}
        listing["marketplace_category_ids"] = {"mercari": "a-different-vendoo-category"}
        self.assertEqual(contract.gaps(listing, "mercari"), [])

    def test_a_valid_looking_option_without_evidence_still_blocks_listing(self):
        listing = seed()
        listing["mercari_specifics"]["material"] = "cotton"
        self.assertIn("evidence", contract.gaps(listing, "mercari")[0]["message"])
        listing[contract.EVIDENCE_KEY] = {"mercari_specifics.material": {"value": "wool", "quote": "100% wool"}}
        self.assertIn("evidence", contract.gaps(listing, "mercari")[0]["message"])

    def test_a_missing_form_blocks_instead_of_using_reference_fields(self):
        listing = seed()
        listing[contract.KEY] = contract.capture(listing, {}, ["mercari"])
        self.assertTrue(contract.gaps(listing, "mercari")[0]["blocking"])
        listing["mercari_specifics"]["material"] = "cotton"
        contract.constrain_generated(listing, listing)
        self.assertNotIn("material", listing["mercari_specifics"])

    def test_category_changes_clear_carried_answers_and_block_stale_choices(self):
        previous = seed()
        previous["mercari_specifics"]["material"] = "cotton"
        updated = copy.deepcopy(previous)
        updated["direct_category_ids"]["mercari"] = "13"
        contract.invalidate_dependents(previous, updated)
        self.assertNotIn("material", updated["mercari_specifics"])
        self.assertIn("changed", contract.gaps(updated, "mercari")[0]["message"])

    def test_quote_must_come_from_original_evidence_not_the_generated_listing(self):
        rows = {row["path"]: row for row in seed()[contract.KEY]["mercari"]["fields"]}
        path = "mercari_specifics.material"
        self.assertEqual(validated_values({"fields": {path: {"value": "Wool", "evidence": "100% wool"}}}, rows,
                                         evidence="Tag reads 100% cotton."), {})
        self.assertEqual(validated_values({"fields": {path: "Cotton"}}, rows, evidence="100% cotton"), {})
        accepted = validated_values({"fields": {path: {"value": "Cotton", "evidence": "100% cotton"}}}, rows,
                                    evidence="Tag reads 100% cotton.")
        self.assertEqual(accepted[path][0], "cotton")

    def test_prompt_contains_only_the_captured_paths_and_choices(self):
        text = contract.prompt(seed()[contract.KEY])
        self.assertIn("mercari_specifics.material", text)
        self.assertIn("Cotton", text)
        self.assertNotIn("inventedField", text)


class NativeContractRevisionTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.conv = ConversationRepo(self.db).create(title="Pants")
        self.repo = ListingRepo(self.db)
        self.repo.save_revision(self.conv.id, seed(), "native_schema")

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_later_model_repairs_cannot_reintroduce_unlisted_fields_or_spoof_the_contract(self):
        result = self.repo.save_revision(self.conv.id, {"title": "Pants", contract.KEY: {},
            "mercari_specifics": {"material": "Leather", "unknown": "guess"}}, "generation_finalize")
        self.assertEqual(result.listing_json[contract.KEY], seed()[contract.KEY])
        self.assertEqual(result.listing_json["mercari_specifics"], {})

    def test_a_model_cannot_declare_its_guess_seller_verified(self):
        result = self.repo.save_revision(self.conv.id, {
            "mercari_specifics": {"material": "cotton"}, "seller_fields": ["mercari_specifics.material"],
            contract.EVIDENCE_KEY: {"mercari_specifics.material": {"value": "cotton", "quote": "invented"}},
        }, "generation_finalize")
        self.assertNotIn("seller_fields", result.listing_json)
        self.assertIn("evidence", contract.gaps(result.listing_json, "mercari")[0]["message"])

    def test_seller_category_edit_keeps_a_blocker_until_its_form_is_reloaded(self):
        listing = seed()
        listing["direct_category_ids"]["mercari"] = "13"
        result = self.repo.save_revision(self.conv.id, listing, "user_form")
        self.assertIn("changed", contract.gaps(result.listing_json, "mercari")[0]["message"])

    def test_initial_generation_uses_native_fields_instead_of_generic_field_prompts(self):
        from vendoo_studio.services.chat_prompts import listing_generation_messages

        with patch("vendoo_studio.services.chat_prompts.learned_fields_prompt") as learned, \
                patch("vendoo_studio.services.chat_prompts._item_form_prompt") as generic:
            messages = listing_generation_messages("Canonical prose rules", "", "Tag reads 100% cotton", self.db,
                                                    self.conv.id, native_forms=seed()[contract.KEY])
        learned.assert_not_called()
        generic.assert_not_called()
        text = messages[0]["content"]
        self.assertEqual(text.count("--- Authoritative native marketplace forms ---"), 1)
        self.assertIn("mercari_specifics.material", text)
        self.assertIn("Canonical prose rules", text)
