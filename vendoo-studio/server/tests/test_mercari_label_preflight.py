"""Mercari's Shipping label: one rule for "answered" across every pre-flight check.

The listing's own label id wins. A saved carrier is a preference: where this
package's labels are known (a captured form) it counts only when Mercari offers
it; where they are not (the form the readiness check builds) it is said as what
List will try, not reported as an empty required field.
"""
from __future__ import annotations

import copy

import pytest

from vendoo_studio.services import direct_preflight, listing_readiness, native_contract
from vendoo_studio.services.mercari_shipping_label import LabelAnswer, label_answer

LABEL = "mercari_specifics.shippingClassId"
SAVED = {"id": "2551", "label": "USPS Ground Advantage"}


def snapshot(*, specifics: dict | None = None, form: dict | None = None) -> dict:
    """The shape of listing b2d0b9f27f87 as pre-flight saw it: prepaid label, shoebox yes, no label id."""
    return {
        "title": "Graphic Tee", "description": "Big Cotton graphic tee.", "price": 20, "condition": "Good",
        "weight_oz": 9, "package_dimensions_in": "13x10x3",
        "direct_category_ids": {"mercari": "303"},
        "mercari_specifics": {"package": {"fitsShoebox": "yes"}, "smartPricing": {}, **(specifics or {})},
        "form": {"overrides": {}, "ships_from_zip": "70115", **(form or {})},
    }


def label_gaps(listing: dict) -> list[dict]:
    return [item for item in direct_preflight.gaps(listing, "mercari") if item["field"] == LABEL]


def missing_label(listing: dict) -> bool:
    return any(item["field"] == LABEL for item in listing_readiness.missing(direct_preflight.gaps(listing, "mercari")))


def test_a_saved_carrier_is_not_reported_as_an_empty_required_label():
    listing = snapshot(form={"mercari_shipping_label": SAVED})

    found = label_gaps(listing)

    assert [item["message"] for item in found if "Required field is empty" in item["message"]] == []
    assert len(found) == 1
    assert found[0]["blocking"] is False and not found[0].get("required")
    # It says what List will try; it does not claim Mercari offers it for this package.
    assert "USPS Ground Advantage" in found[0]["message"] and "if Mercari offers it" in found[0]["message"]
    assert "checked when you list" in found[0]["message"]
    assert not missing_label(listing)


def test_no_label_and_no_saved_carrier_still_holds_list():
    listing = snapshot()

    found = label_gaps(listing)

    assert found and found[0]["blocking"] is True
    assert found[0]["message"] == "Mercari needs a prepaid shipping label."
    assert missing_label(listing)


@pytest.mark.parametrize("own", ["2551", 2551, "77"])
def test_the_listings_own_label_wins_over_the_saved_carrier(own):
    listing = snapshot(specifics={LABEL.rsplit(".", 1)[-1]: own}, form={"mercari_shipping_label": SAVED})

    assert label_answer(listing) == LabelAnswer("listing", str(own))
    assert label_gaps(listing) == []


def test_a_label_the_prepared_form_resolved_answers_the_row():
    listing = snapshot(form={"specifics": {"shipping": {"carrierId": 2551}}})

    assert label_answer(listing) == LabelAnswer("form", "2551")
    assert label_gaps(listing) == []


@pytest.mark.parametrize("value", [0, "0", "", None, False, True, {"id": 1}])
def test_ship_on_your_own_and_blank_ids_are_not_a_label(value):
    listing = snapshot(specifics={"shippingClassId": value})

    assert label_answer(listing) == LabelAnswer("none", "")


def test_ship_on_your_own_needs_no_label_even_with_a_saved_carrier():
    listing = snapshot(specifics={"shippingMethod": "SOYO"}, form={"mercari_shipping_label": SAVED})

    assert label_gaps(listing) == []


def label_row(**options: str) -> dict:
    return {"path": LABEL, "key": LABEL, "label": "Shipping label", "required": True, "selection_only": True,
            "options": list(options), "option_values": dict(options)}


def with_contract(listing: dict, row: dict) -> dict:
    """A Mercari form captured for this listing's package, holding the labels Mercari quoted then."""
    captured = copy.deepcopy(listing)
    fields = [row]
    captured[native_contract.KEY] = {"mercari": {"fields": fields,
                                                 "inputs": native_contract.inputs(captured, "mercari", fields)}}
    return captured


def contract_label_gaps(listing: dict) -> list[dict]:
    return [item for item in native_contract.gaps(listing, "mercari") if item["field"] == LABEL]


def test_a_captured_form_that_offers_the_saved_carrier_answers_the_label():
    listing = with_contract(snapshot(form={"mercari_shipping_label": SAVED}),
                            label_row(**{"USPS Ground Advantage": "2553", "UPS Ground Saver": "2610"}))

    # Offered under this weight tier's own id, not the id it was saved with.
    assert contract_label_gaps(listing) == []
    assert not missing_label(listing)


def test_a_captured_form_that_does_not_offer_the_saved_carrier_says_so():
    listing = with_contract(snapshot(form={"mercari_shipping_label": SAVED}),
                            label_row(**{"UPS Ground Saver": "2610"}))

    found = contract_label_gaps(listing)

    assert len(found) == 1 and found[0]["blocking"] is True
    assert "USPS Ground Advantage" in found[0]["message"] and "isn't offered" in found[0]["message"]
    assert missing_label(listing)


def test_a_captured_form_without_the_saved_carrier_says_list_uses_ground_advantage():
    listing = with_contract(snapshot(form={"mercari_shipping_label": {"id": "2610", "label": "UPS Ground Saver"}}),
                            label_row(**{"USPS Ground Advantage": "2553", "FedEx": "2700"}))

    found = contract_label_gaps(listing)

    # The form already shows Ground Advantage and List sends it, so this is said, not held.
    assert len(found) == 1 and found[0]["blocking"] is False
    assert "UPS Ground Saver" in found[0]["message"] and "List uses USPS Ground Advantage" in found[0]["message"]
    assert not missing_label(listing)


def test_a_captured_form_with_no_label_and_no_saved_carrier_is_still_empty():
    listing = with_contract(snapshot(), label_row(**{"USPS Ground Advantage": "2553"}))

    found = contract_label_gaps(listing)

    assert [item["message"] for item in found] == ["Shipping label: Required field is empty."]
    assert found[0]["blocking"] is True


def test_a_captured_form_still_refuses_a_listing_label_it_does_not_offer():
    listing = with_contract(snapshot(specifics={"shippingClassId": "9999"}, form={"mercari_shipping_label": SAVED}),
                            label_row(**{"USPS Ground Advantage": "2553"}))

    found = contract_label_gaps(listing)

    assert len(found) == 1 and found[0]["blocking"] is True
    assert "current options" in found[0]["message"]


def test_a_captured_form_checks_a_label_the_prepared_form_resolved():
    offered = label_row(**{"USPS Ground Advantage": "2553"})
    assert contract_label_gaps(with_contract(
        snapshot(form={"specifics": {"shipping": {"carrierId": "2553"}}}), offered)) == []
    found = contract_label_gaps(with_contract(
        snapshot(form={"specifics": {"shipping": {"carrierId": "1"}}}), offered))
    assert found and "current options" in found[0]["message"]


def test_a_captured_ship_on_your_own_row_never_asks_for_the_saved_carrier():
    row = {**label_row(**{"UPS Ground Saver": "2610"}), "required": False}
    listing = with_contract(snapshot(specifics={"shippingMethod": "SOYO"}, form={"mercari_shipping_label": SAVED}), row)

    assert contract_label_gaps(listing) == []
