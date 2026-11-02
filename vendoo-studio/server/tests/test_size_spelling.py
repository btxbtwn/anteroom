"""The same letter size spelled each marketplace's way: Depop's XXL is eBay's 2XL."""

import pytest

from vendoo_studio.services.ebay_fields import ebay_size_for_type
from vendoo_studio.models.sizes import letter_size, size_choice
from vendoo_studio.services.validation import validate_listing
from vendoo_studio.services.direct_preflight import _unresolved
from vendoo_studio.services.listing_generate import align_size_fields
from vendoo_studio.services.vendoo_specifics import FieldSpec, encode_scaled, encode_specific

EBAY_SIZES = {size: size for size in ("XS", "S", "M", "L", "XL", "2XL", "3XL", "One Size")}


@pytest.mark.parametrize(("size", "letters"), [
    ("XXL", "2XL"), ("2X", "2XL"), ("xx-large", "2XL"), ("XXXL", "3XL"), ("XXS", "2XS"), ("Large", "L"), ("M", "M"),
])
def test_letter_sizes_read_as_counted_letters(size, letters):
    assert letter_size(size) == letters


@pytest.mark.parametrize("size", ["One Size", "34", "XL (18/20)", "IT 48", ""])
def test_other_sizes_are_not_letter_sizes(size):
    assert letter_size(size) is None


def test_ebay_spells_a_general_xxl_as_2xl():
    assert ebay_size_for_type("XXL", "Regular") == "2XL"
    assert ebay_size_for_type("One Size", "Regular") == "One Size"


def test_saving_puts_ebays_spelling_on_the_ebay_form_and_keeps_the_general_size():
    listing = {"title": "Columbia XXL Fleece Jacket", "brand": "Columbia", "size": "XXL",
               "ebay_specifics": {"size": "XXL", "sizeType": "Regular"}}
    align_size_fields(listing)
    assert listing["ebay_specifics"]["size"] == "2XL"
    assert listing["size"] == "XXL"


def test_an_ebay_size_spelled_the_ebay_way_matches_the_general_size():
    listing = {"title": "Columbia XXL Fleece Jacket", "brand": "Columbia", "size": "XXL",
               "ebay_specifics": {"size": "2XL", "sizeType": "Regular"}}
    result = validate_listing(listing, require_photos=False, selected_marketplaces=["ebay"])
    assert not any(issue["field"] == "ebay_specifics.size" for issue in [*result.errors, *result.warnings])


def test_a_size_is_matched_to_the_marketplaces_spelling_of_it():
    spec = FieldSpec("Size", display="Size", selection_only=True, options=EBAY_SIZES)
    assert encode_specific(spec, "XXL") == ("2XL", True)
    assert encode_specific(spec, "Huge") == ("", False)


def test_only_size_fields_match_by_spelling():
    spec = FieldSpec("Fit", display="Fit", selection_only=True, options={"2XL": "2XL"})
    assert encode_specific(spec, "XXL") == ("", False)


def test_a_scaled_size_is_matched_to_the_marketplaces_spelling_of_it():
    spec = FieldSpec("size", display="Size", scales={"standard": {"display": "Standard", "options": {"xxl": "XXL"}}})
    assert encode_scaled(spec, "2XL") == ("standard", "xxl", True)


def test_a_required_field_left_empty_is_not_called_a_refused_choice():
    snapshot = {"form": {"unresolved": [{"field": "ebay:Size", "value": "Huge"}, {"field": "ebay:Size", "value": ""},
                                        {"field": "ebay:Color", "value": ""}]}}
    messages = [item["message"] for item in _unresolved(snapshot, "ebay")]
    assert messages == ["Size “Huge” is not one of eBay's choices, so it is left empty.", "eBay needs Color, and it is empty."]


CHOICES = ["2XS", "XS", "S", "M", "L", "XL", "2XL", "3XL", "One Size", "28", "30"]


@pytest.mark.parametrize(("size", "choice"), [
    ("XXL", "2XL"), ("2X", "2XL"), ("xl", "XL"), ("OS", "One Size"), ("one-size", "One Size"),
    ("x-large", "XL"), ("XL (18/20)", "XL"), ("30", "30"), ("32", None), ("Huge", None),
])
def test_a_size_is_found_among_the_marketplaces_choices(size, choice):
    assert size_choice(size, CHOICES) == choice


def test_a_kids_range_matches_only_the_same_range_or_a_plain_letter():
    assert size_choice("XL (18/20)", ["XL (18/20)", "XL"]) == "XL (18/20)"
    assert size_choice("XL (18/20)", ["L", "XL (14/16)"]) is None


def test_the_ebay_form_takes_the_size_from_its_categorys_own_choices():
    from vendoo_studio.services.category_fields import save_fields

    save_fields("ebay", "990001", {"Size": FieldSpec("Size", display="Size", selection_only=True,
                                                     options={label: label for label in CHOICES})})
    listing = {"title": "Nike OS Cap", "brand": "Nike", "size": "OS",
               "marketplace_category_ids": {"ebay": "990001"}, "ebay_specifics": {"size": "OS"}}
    align_size_fields(listing)
    assert listing["ebay_specifics"]["size"] == "One Size"
    result = validate_listing(listing, require_photos=False, selected_marketplaces=["ebay"])
    assert not any(issue["field"] == "ebay_specifics.size" for issue in [*result.errors, *result.warnings])


WAIST_SIZES = ['26"', '27"', '36"', '38"']


@pytest.mark.parametrize(("size", "choices", "choice"), [
    ("36", WAIST_SIZES, '36"'), ('36"', WAIST_SIZES, '36"'), ("Waist 36", WAIST_SIZES, '36"'),
    ("Waist 36 in.", ["36", "38"], "36"), ("36", ["36", "38"], "36"),
])
def test_a_measurement_keeps_its_size_with_or_without_the_mark(size, choices, choice):
    assert size_choice(size, choices) == choice


def test_a_plain_number_keeps_the_plain_choice_when_the_list_has_both():
    assert size_choice("36", ["36", '36"']) == "36"


def test_a_number_that_is_no_size_of_this_list_matches_nothing():
    assert size_choice("36", ["M", "L"]) is None


def test_depop_spells_a_mans_waist_with_inches_on_its_form_and_in_the_listing():
    listing = {"title": "Wrangler Straight Leg Jeans 36 Blue Denim", "brand": "Wrangler", "size": "36",
               "description": "Blue jeans.", "price": 25, "condition": "Pre-Owned - Good",
               "category_path": "Men > Bottoms > Jeans", "direct_category_ids": {"depop": "menswear_jeans"}}
    align_size_fields(listing)
    assert listing["depop_specifics"]["size"] == '36"'
    assert listing["size"] == "36"


def test_the_depop_form_offers_the_size_it_spells_for_the_category():
    from vendoo_studio.services import depop_form

    row = next(row for row in depop_form.form({"direct_category_ids": {"depop": "menswear_jeans"}, "size": "36"})["fields"]
               if row["path"] == "depop_specifics.size")
    assert row["value"] == '36"'
    assert row["value"] in row["options"]
