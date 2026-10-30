from __future__ import annotations

import copy
import json
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


def ebay_tshirt():
    listing = {"title": "Chase Authentics XL Y2K Tony Stewart #20 NASCAR T-Shirt Black Straight Cut",
               "brand": "Chase Authentics", "condition": "Pre-Owned - Good",
               "category_path": "Clothing, Shoes & Accessories > Men > Men's Clothing > Shirts & T-Shirts",
               "direct_category_ids": {"ebay": "15687"}, "ebay_specifics": {"condition": "3000"}}
    form = {"marketplace": "ebay", "native": True, "known": True, "page_labels": True, "category_id": "15687",
            "fields": [
                {"path": "ebay_specifics.condition", "label": "Item condition", "options": ["Pre-Owned - Good"],
                 "option_values": {"Pre-Owned - Good": "3000"}},
                {"path": "ebay_specifics.brand", "label": "Brand", "options": ["Chase Authentics"],
                 "option_values": {"Chase Authentics": "Chase Authentics"}},
                {"path": "ebay_specifics.style", "label": "Style", "category_specific": True, "options": ["Graphic Tee"],
                 "option_values": {"Graphic Tee": "Graphic Tee"}},
            ]}
    listing[contract.KEY] = contract.capture(listing, {"ebay": form}, ["ebay"])
    return listing


class NativeContractTest(unittest.TestCase):
    def test_editor_shows_the_same_inherited_size_type_that_validation_accepts(self):
        listing = ebay_tshirt()
        listing["sizeType"] = "Regular"
        row = {"path": "ebay_specifics.Size Type", "label": "Size Type", "required": True,
               "selection_only": True, "options": ["Regular", "Big & Tall"]}
        form = {"category_id": "15687", "page_labels": True, "fields": [row]}
        listing[contract.KEY] = contract.capture(listing, {"ebay": form}, ["ebay"])
        rendered = contract.editor_form(listing, "ebay")
        self.assertEqual(rendered["fields"][0]["value"], "Regular")
        listing["ebay_specifics"]["Size Type"] = ""
        self.assertEqual(contract.editor_form(listing, "ebay")["fields"][0]["value"], "")
        self.assertTrue(any(gap["field"] == row["path"] for gap in contract.gaps(listing, "ebay")))
        listing["seller_fields"] = ["ebay_specifics.Size Type"]
        self.assertEqual(contract.editor_form(listing, "ebay")["fields"][0]["value"], "")
        self.assertTrue(any(gap["field"] == row["path"] for gap in contract.gaps(listing, "ebay")))

    def test_validate_route_keeps_native_size_before_checking_it(self):
        from vendoo_studio.routes.listings import validate

        listing = {"brand": "Hanes", "size": "XL", "ebay_specifics": {"Size": "XL"}}
        form = {"category_id": "15687", "fields": [{"path": "ebay_specifics.Size", "label": "Size",
                 "required": True, "selection_only": True, "options": ["XL"]}]}
        listing[contract.KEY] = contract.capture(listing, {"ebay": form}, ["ebay"])
        listing[contract.EVIDENCE_KEY] = {"ebay_specifics.Size": {"value": "XL", "quote": "Size XL"}}
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        try:
            with sessionmaker(bind=engine)() as db:
                conv = ConversationRepo(db).create(title="Tee")
                ListingRepo(db).save_revision(conv.id, listing, source="native_schema")
                result = validate(conv.id, db)
                self.assertFalse(any(error["field"] == "ebay_specifics.Size" for error in result.errors))
        finally:
            engine.dispose()

    def test_price_edit_preserves_other_native_marketplace_answers(self):
        from vendoo_studio.services.listing_editor import prepared_listing

        listing = {"price": 20, "size": "XL", "brand": "Hanes",
                   "ebay_specifics": {"Size": "XL", "Department": "Men"},
                   "depop_specifics": {"size": "XL"},
                   contract.KEY: {"ebay": {}, "depop": {}}}
        submitted = copy.deepcopy(listing)
        submitted["poshmark_specifics"] = {"poshmarkPrice": 21}
        updated = prepared_listing(listing, submitted)
        self.assertEqual(updated["ebay_specifics"], listing["ebay_specifics"])
        self.assertEqual(updated["depop_specifics"], listing["depop_specifics"])

    def test_unrelated_edit_does_not_invent_etsy_fields_for_an_ineligible_item(self):
        from vendoo_studio.services.listing_editor import prepared_listing

        listing = seed()
        listing["etsy_specifics"] = {"who_made": "Another company or person", "when_made": "2020 - 2026 (Recently)"}
        submitted = copy.deepcopy(listing)
        submitted.setdefault("poshmark_specifics", {})["poshmarkPrice"] = 25
        updated = prepared_listing(listing, submitted)
        self.assertEqual(updated["etsy_specifics"], listing["etsy_specifics"])

    def test_unrelated_edit_does_not_autofill_an_eligible_etsy_form(self):
        from vendoo_studio.models.validation import validate_listing
        from vendoo_studio.services.listing_editor import prepared_listing

        listing = seed()
        listing["etsy_specifics"] = {"who_made": "Another company or person", "what_is": "A finished product",
                                    "when_made": "2000 - 2006 (Vintage)"}
        submitted = copy.deepcopy(listing)
        submitted.setdefault("poshmark_specifics", {})["poshmarkPrice"] = 25
        updated = prepared_listing(listing, submitted)
        validation = validate_listing(updated, require_photos=False, selected_marketplaces=["etsy"])
        self.assertEqual(updated["etsy_specifics"], listing["etsy_specifics"])
        self.assertFalse(any(issue["field"].startswith("etsy_specifics.") for issue in validation.errors), validation.errors)

    def test_marketplace_price_override_belongs_to_the_native_price_row(self):
        listing = {"price": 20, "poshmark_specifics": {"poshmarkPrice": 21}}
        form = {"category_id": "shirts", "fields": [{"path": "price", "label": "Listing Price", "required": True}]}
        listing[contract.KEY] = contract.capture(listing, {"poshmark": form}, ["poshmark"])
        self.assertEqual(contract.gaps(listing, "poshmark"), [])

    def test_copy_generation_receives_the_chosen_native_category_without_its_siblings(self):
        from vendoo_studio.services import mercari_form

        listing = {"direct_category_ids": {"mercari": "275"}}
        contracts = contract.capture(listing, {"mercari": mercari_form.form(listing)}, ["mercari"])
        before = copy.deepcopy(contracts)
        text = contract.prompt(contracts)
        projected = json.loads(text[text.index("{"):])
        category = next(row for row in projected["mercari"]["fields"] if row["path"] == "direct_category_ids.mercari")
        self.assertEqual(category["options"], ["Women > Maternity > Sweaters"])
        self.assertEqual(category["option_values"], {"Women > Maternity > Sweaters": "275"})
        self.assertLess(len(text), 20000)
        self.assertEqual(contracts, before)

    def test_large_brand_catalog_stays_in_editor_and_is_searchable_in_prompt(self):
        from vendoo_studio.services import depop_form

        for brand in ("nike", "", "Not a native brand"):
            listing = {"brand": brand}
            contracts = contract.capture(listing, {"depop": depop_form.form(listing)}, ["depop"])
            before = copy.deepcopy(contracts)
            text = contract.prompt(contracts)
            projected = json.loads(text[text.index("{"):])
            field = next(row for row in projected["depop"]["fields"] if row["path"] == "depop_specifics.brand")
            self.assertNotIn("options", field)
            self.assertEqual(field["options_lookup"]["count"], 21697)
            self.assertEqual(contracts, before)
            self.assertLess(len(json.dumps(field)), 500)
            self.assertEqual(len(next(row for row in contracts["depop"]["fields"]
                                      if row["path"] == "depop_specifics.brand")["options"]), 21697)

    def test_large_aspect_catalog_is_narrowed_without_changing_validation(self):
        labels = [f"Player {i}" for i in range(5000)] + ["Tony Stewart"]
        row = {"path": "ebay_specifics.player", "label": "Player", "options": labels,
               "option_values": {label: str(i) for i, label in enumerate(labels)},
               "value_constraints": {label: [{"parentName": "Sport", "parentValue": "Racing"}]
                                     for label in labels}, "selection_only": True}
        contracts = {"ebay": {"inputs": {}, "fields": [row]}}
        before = copy.deepcopy(contracts)
        text = contract.prompt(contracts)
        field = json.loads(text[text.index("{"):])["ebay"]["fields"][0]
        self.assertNotIn("options", field)
        self.assertNotIn("option_values", field)
        self.assertNotIn("value_constraints", field)
        self.assertEqual(field["options_lookup"]["count"], 5001)
        self.assertLess(len(text), 12000)
        self.assertEqual(contracts, before)
        self.assertIsNone(contract.value_error(row, "Player 4999"))

    def test_marketplace_overrides_do_not_make_the_saved_form_stale(self):
        from vendoo_studio.services.seller_overrides import apply_marketplace_overrides

        listing = seed()
        listing.update(brand="General brand", condition="Pre-Owned - Good")
        listing["mercari_specifics"].update(brand="Anne Klein", condition="3")
        fields = listing[contract.KEY]["mercari"]["fields"]
        captured = contract.inputs(listing, "mercari", fields)
        prepared = copy.deepcopy(listing)
        apply_marketplace_overrides(prepared, "mercari")
        self.assertEqual(prepared["brand"], "Anne Klein")
        self.assertEqual(captured, contract.inputs(prepared, "mercari", fields))
        listing["mercari_specifics"]["condition"] = "4"
        self.assertNotEqual(captured, contract.inputs(listing, "mercari", fields))

    def test_general_condition_normalization_does_not_make_the_saved_form_stale(self):
        listing = seed()
        listing["condition"] = "Good"
        fields = listing[contract.KEY]["mercari"]["fields"]
        captured = contract.inputs(listing, "mercari", fields)
        listing["condition"] = "Pre-Owned - Good"
        self.assertEqual(captured, contract.inputs(listing, "mercari", fields))

    def test_preparing_the_shared_category_does_not_make_the_saved_form_stale(self):
        from vendoo_studio.services.registry import map_vendoo_category_path

        listing = ebay_tshirt()
        prepared = copy.deepcopy(listing)
        prepared["category_path"] = map_vendoo_category_path(prepared["category_path"], prepared)
        self.assertNotEqual(prepared["category_path"], listing["category_path"])
        self.assertEqual(contract.gaps(prepared, "ebay"), [])
        self.assertIsNotNone(contract.editor_form(prepared, "ebay"))

    def test_a_form_saved_with_another_spelling_of_no_answer_still_opens(self):
        # One writer stores the unset choice as "", another leaves the key out,
        # and an unset boolean arrives as False. None is a choice the seller made.
        for spelling in ("", None, False):
            with self.subTest(spelling=spelling):
                listing = ebay_tshirt()
                inputs = listing[contract.KEY]["ebay"]["inputs"]
                inputs["ebay_specifics.Size Type"] = spelling
                listing["ebay_specifics"].pop("sizeType", None)
                self.assertTrue(contract.inputs_match(
                    listing, "ebay", listing[contract.KEY]["ebay"]["fields"], inputs))
                self.assertEqual(contract.gaps(listing, "ebay"), [])

    def test_a_changed_answer_still_makes_a_no_answer_form_stale(self):
        listing = ebay_tshirt()
        listing[contract.KEY]["ebay"]["inputs"]["ebay_specifics.Size Type"] = ""
        listing["sizeType"] = "Petite"
        listing[contract.KEY]["ebay"]["fields"].append(
            {"path": "ebay_specifics.Size Type", "label": "Size Type", "required": True,
             "options": ["Regular", "Petite"], "option_values": {"Regular": "Regular", "Petite": "Petite"}})
        self.assertFalse(contract.inputs_match(
            listing, "ebay", listing[contract.KEY]["ebay"]["fields"], listing[contract.KEY]["ebay"]["inputs"]))

    def test_a_form_recorded_with_the_raw_category_path_still_opens(self):
        from vendoo_studio.services.registry import map_vendoo_category_path

        listing = ebay_tshirt()
        raw = listing["category_path"]
        listing[contract.KEY]["ebay"]["inputs"]["category_path"] = raw
        listing["category_path"] = map_vendoo_category_path(raw, listing)
        self.assertEqual(contract.gaps(listing, "ebay"), [])
        self.assertIsNotNone(contract.editor_form(listing, "ebay"))

    def test_a_form_recorded_with_general_brand_and_condition_still_opens(self):
        listing = ebay_tshirt()
        # Before posting compared marketplace overrides, the contract stored the
        # General answers even though the eBay form carried its own condition id.
        listing[contract.KEY]["ebay"]["inputs"]["brand"] = listing["brand"]
        listing[contract.KEY]["ebay"]["inputs"]["condition"] = listing["condition"]
        self.assertEqual(contract.gaps(listing, "ebay"), [])
        self.assertIsNotNone(contract.editor_form(listing, "ebay"))

    def test_ebay_condition_label_and_id_are_the_same_controlling_choice(self):
        listing = ebay_tshirt()
        listing["ebay_specifics"].pop("condition")
        self.assertEqual(contract.gaps(listing, "ebay"), [])
        self.assertEqual(contract.changed_inputs(listing, "ebay", listing[contract.KEY]["ebay"]["fields"],
                                                 listing[contract.KEY]["ebay"]["inputs"]), set())
        self.assertIsNotNone(contract.editor_form(listing, "ebay"))
        listing["condition"] = "Pre-Owned - Excellent"
        self.assertIsNone(contract.editor_form(listing, "ebay"))

    def test_a_changed_condition_still_makes_a_legacy_form_stale(self):
        listing = ebay_tshirt()
        listing[contract.KEY]["ebay"]["inputs"]["condition"] = listing["condition"]
        listing["condition"] = "Pre-Owned - Excellent"
        listing["ebay_specifics"]["condition"] = "4000"
        self.assertIn("changed", contract.gaps(listing, "ebay")[0]["message"])
        self.assertIsNone(contract.editor_form(listing, "ebay"))

    def test_preparing_the_shared_category_does_not_clear_its_answers(self):
        from vendoo_studio.services.registry import map_vendoo_category_path

        previous = ebay_tshirt()
        previous["ebay_specifics"]["style"] = "Graphic Tee"
        updated = copy.deepcopy(previous)
        updated["category_path"] = map_vendoo_category_path(updated["category_path"], updated)
        contract.invalidate_dependents(previous, updated)
        self.assertEqual(updated["ebay_specifics"]["style"], "Graphic Tee")

    def test_validation_only_contract_is_not_rendered_as_a_complete_editor(self):
        listing = seed()
        listing[contract.KEY]["mercari"].pop("page_labels")
        self.assertIsNone(contract.editor_form(listing, "mercari"))

    def test_editor_metadata_is_preserved_without_bloating_the_generation_prompt(self):
        listing = seed()
        form = {"category_id": "12", "page_labels": True, "fields": [{
            "path": "mercari_specifics.material", "label": "Material", "section": "Product info",
            "width": "half", "options": ["Cotton"], "option_values": {"Cotton": "cotton"},
            "cascade": [{"label": "editor-only category tree"}],
        }]}
        listing[contract.KEY] = contract.capture(listing, {"mercari": form}, ["mercari"])
        listing["mercari_specifics"]["material"] = "cotton"
        rendered = contract.editor_form(listing, "mercari")
        self.assertEqual(rendered["fields"][0]["section"], "Product info")
        self.assertTrue(rendered["page_labels"])
        rendered["fields"][0]["options"].append("Wool")
        self.assertEqual(listing[contract.KEY]["mercari"]["fields"][0]["options"], ["Cotton"])
        self.assertNotIn("editor-only category tree", contract.prompt(listing[contract.KEY]))
        self.assertIn("Cotton", contract.prompt(listing[contract.KEY]))

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

    def test_optional_answers_do_not_repeat_evidence_reminders(self):
        listing = seed()
        listing["mercari_specifics"]["material"] = "cotton"
        listing[contract.KEY]["mercari"]["fields"][0]["required"] = False
        self.assertEqual(contract.gaps(listing, "mercari"), [])
        listing["mercari_specifics"]["material"] = "not an allowed material"
        self.assertTrue(contract.gaps(listing, "mercari"))

    def test_an_aspect_stored_under_another_spelling_answers_its_row(self):
        listing = ebay_tshirt()
        # The listing JSON keeps the aspect lowercase; the form names the row Style.
        row = next(row for row in listing[contract.KEY]["ebay"]["fields"] if row["path"] == "ebay_specifics.style")
        row["path"] = "ebay_specifics.Style"
        row["required"] = True
        listing[contract.KEY]["ebay"]["inputs"] = contract.inputs(
            listing, "ebay", listing[contract.KEY]["ebay"]["fields"])
        listing["ebay_specifics"]["style"] = "Graphic Tee"
        listing[contract.EVIDENCE_KEY] = {"ebay_specifics.style": {"value": "Graphic Tee", "quote": "graphic tee"}}
        self.assertEqual(contract.gaps(listing, "ebay"), [])

    def test_a_required_aspect_general_already_answers_is_not_reported_empty(self):
        listing = {"title": "Beige pants", "brand": "Anne Klein", "size": "M", "department": "Women",
                   "condition": "Pre-Owned - Good", "direct_category_ids": {"ebay": "63863"},
                   "ebay_specifics": {"condition": "3000"}}
        form = {"marketplace": "ebay", "native": True, "known": True, "page_labels": True, "category_id": "63863",
                "fields": [
                    {"path": "ebay_specifics.condition", "label": "Item condition", "options": ["Pre-Owned - Good"],
                     "option_values": {"Pre-Owned - Good": "3000"}},
                    {"path": "ebay_specifics.Brand", "label": "Brand", "required": True,
                     "options": ["Anne Klein"], "option_values": {"Anne Klein": "Anne Klein"}},
                    {"path": "ebay_specifics.Size", "label": "Size", "required": True,
                     "selection_only": True, "options": ["S", "M", "L"],
                     "option_values": {"S": "S", "M": "M", "L": "L"}},
                    {"path": "ebay_specifics.Department", "label": "Department", "required": True,
                     "options": ["Women"], "option_values": {"Women": "Women"}},
                ]}
        listing[contract.KEY] = contract.capture(listing, {"ebay": form}, ["ebay"])
        # eBay's posting path fills these aspects from the listing's own fields.
        self.assertEqual(contract.gaps(listing, "ebay"), [])

    def test_a_start_bid_is_not_required_for_a_fixed_price_listing(self):
        row = {"path": "ebay_specifics.auctionStartPrice", "required": True}
        fixed = {"ebay_specifics": {"listingFormat": "FIXED_PRICE"}}
        auction = {"ebay_specifics": {"listingFormat": "AUCTION"}}
        self.assertFalse(contract.required_for_this_listing(row, fixed, row["path"]))
        self.assertTrue(contract.required_for_this_listing(row, auction, row["path"]))

    def test_a_missing_account_policy_is_noted_not_refused(self):
        listing = ebay_tshirt()
        listing[contract.KEY]["ebay"]["fields"].append(
            {"path": "ebay_specifics.returnPolicyId", "label": "Return policy", "required": True})
        listing["ebay_specifics"].pop("returnPolicyId", None)
        found = [item for item in contract.gaps(listing, "ebay") if item["field"] == "ebay_specifics.returnPolicyId"]
        self.assertTrue(found)
        self.assertFalse(found[0]["blocking"])

    def test_a_field_the_form_no_longer_has_is_noted_not_refused(self):
        listing = ebay_tshirt()
        listing["ebay_specifics"]["obsoleteAspect"] = "Something"
        found = contract.gaps(listing, "ebay")
        self.assertTrue(found)
        self.assertTrue(all(not item["blocking"] for item in found))

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

    def test_generated_ebay_size_aliases_are_saved_at_the_native_paths(self):
        listing = {"ebay_specifics": {"sizeType": "Regular", "size": "36", "waistSize": "36 in"}}
        form = {"fields": [
            {"path": "ebay_specifics.Size Type", "options": ["Regular"], "selection_only": True},
            {"path": "ebay_specifics.Size", "options": ["36"], "selection_only": True},
            {"path": "ebay_specifics.Waist Size", "options": ["36 in"], "selection_only": True},
        ]}
        listing[contract.KEY] = contract.capture(listing, {"ebay": form}, ["ebay"])
        contract.constrain_generated(listing, copy.deepcopy(listing))
        self.assertEqual(listing["ebay_specifics"], {"Size Type": "Regular", "Size": "36", "Waist Size": "36 in"})

    def test_a_size_the_form_spells_with_inches_survives_as_that_choice(self):
        listing = {"title": "Wrangler jeans", "brand": "Wrangler", "size": "36",
                   "direct_category_ids": {"depop": "menswear_jeans"}, "depop_specifics": {"size": "36"}}
        form = {"category_id": "menswear_jeans", "fields": [
            {"path": "depop_specifics.size", "label": "Size", "required": True, "selection_only": True,
             "options": ['26"', '36"', '38"'], "category_specific": True}]}
        listing[contract.KEY] = contract.capture(listing, {"depop": form}, ["depop"])
        original = copy.deepcopy(listing)
        contract.constrain_generated(listing, original)
        self.assertEqual(listing["depop_specifics"]["size"], '36"')
        wrong = copy.deepcopy(original)
        wrong["depop_specifics"] = {"size": "Huge"}
        contract.constrain_generated(wrong, original)
        self.assertEqual(wrong["depop_specifics"], {})

    def test_quote_must_come_from_original_evidence_not_the_generated_listing(self):
        rows = {row["path"]: row for row in seed()[contract.KEY]["mercari"]["fields"]}
        path = "mercari_specifics.material"
        self.assertEqual(validated_values({"fields": {path: {"value": "Wool", "evidence": "100% wool"}}}, rows,
                                         evidence="Tag reads 100% cotton."), {})
        self.assertEqual(validated_values({"fields": {path: "Cotton"}}, rows, evidence="100% cotton"), {})
        accepted = validated_values({"fields": {path: {"value": "Cotton", "evidence": "100% cotton"}}}, rows,
                                    evidence="Tag reads 100% cotton.")
        self.assertEqual(accepted[path][0], "cotton")

    def test_unrelated_edit_preserves_answers_while_category_snapshot_is_stale(self):
        previous = seed()
        previous["direct_category_ids"]["mercari"] = "13"
        previous["mercari_specifics"]["material"] = "cotton"
        updated = copy.deepcopy(previous)
        updated["title"] = "Updated pants title"
        contract.invalidate_dependents(previous, updated)
        self.assertEqual(updated["mercari_specifics"]["material"], "cotton")
        # The stale schema still blocks sending until it is actually refreshed.
        self.assertIn("changed", contract.gaps(updated, "mercari")[0]["message"])

    def test_returning_to_snapshot_category_clears_previous_category_answers(self):
        previous = seed()
        previous["direct_category_ids"]["mercari"] = "13"
        previous["mercari_specifics"]["material"] = "cotton"
        updated = copy.deepcopy(previous)
        updated["direct_category_ids"]["mercari"] = "12"
        contract.invalidate_dependents(previous, updated)
        self.assertNotIn("material", updated["mercari_specifics"])

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
