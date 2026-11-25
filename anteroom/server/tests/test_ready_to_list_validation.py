"""Ready to list must accept drafts marketplaces already treat as Ready (#2097)."""
from __future__ import annotations

import copy

import pytest

from anteroom.services import native_contract, user_settings
from anteroom.services.validation import normalize_listing_dropdowns, validate_listing

SHOEBOX = "mercari_specifics.package.fitsShoebox"

VALID = {
    "title": "Unbranded L Casual Graphic Tee Green Cotton",
    "description": (
        "Casual graphic tee in green cotton.\n\n"
        "Flaws: none noted.\n\n"
        "Measurements: Pit to pit: 20\"; Length: 28\""
    ),
    "price": 14,
    "brand": "Unbranded",
    "size": "L",
    "sku": "TEE-L-1",
    "weight_lb": 0,
    "weight_oz": 8,
    "package_dimensions_in": "13x10x3",
    "department": "Kids",
    "condition": "Pre-Owned - Good",
    "category_path": (
        "Clothing, Shoes & Accessories > Kids > Boys > "
        "Boys' Clothing (Sizes 4 & Up) > Tops, Shirts & T-Shirts"
    ),
}


@pytest.fixture
def settings_file(monkeypatch, tmp_path):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(user_settings, "settings_path", lambda: path)
    return path


@pytest.mark.parametrize("source", [["Preloved"], ["preloved"], "Preloved", "preloved"])
def test_ready_to_list_accepts_depop_source_as_list_or_label_or_code(source):
    listing = copy.deepcopy(VALID)
    listing["depop_specifics"] = {
        "source": copy.deepcopy(source),
        "age": "Modern",
        "style": ["Casual"],
        "parcelSize": "Medium",
        "occasion": ["Casual"],
    }

    result = validate_listing(listing, 1, selected_marketplaces=["depop"])

    assert not any(err["field"] == "depop_specifics.source" for err in result.errors), result.errors
    assert result.valid, result.errors


@pytest.mark.parametrize("age", [["Modern"], ["modern"], "Modern", "y2k"])
def test_ready_to_list_accepts_depop_age_as_list_or_label_or_code(age):
    listing = copy.deepcopy(VALID)
    listing["depop_specifics"] = {
        "source": "Preloved",
        "age": copy.deepcopy(age),
        "style": ["Casual"],
        "parcelSize": "Medium",
        "occasion": ["Casual"],
    }

    result = validate_listing(listing, 1, selected_marketplaces=["depop"])

    assert not any(err["field"] == "depop_specifics.age" for err in result.errors), result.errors
    assert result.valid, result.errors


def test_normalize_keeps_depop_source_list_shape_and_canonical_labels():
    listing = {"depop_specifics": {"source": ["preloved"], "age": ["y2k"]}}

    assert normalize_listing_dropdowns(listing) is True
    assert listing["depop_specifics"]["source"] == ["Preloved"]
    assert listing["depop_specifics"]["age"] == ["y2k"]


def test_normalize_keeps_every_depop_source_from_a_comma_separated_scalar():
    listing = {"depop_specifics": {"source": "vintage, preloved"}}

    assert normalize_listing_dropdowns(listing) is True
    assert listing["depop_specifics"]["source"] == ["Vintage", "Preloved"]


def test_ready_to_list_uses_saved_mercari_shoebox_default(settings_file):
    listing = copy.deepcopy(VALID)
    listing["direct_category_ids"] = {"mercari": "303"}
    listing["mercari_specifics"] = {}
    listing[native_contract.KEY] = native_contract.capture(
        listing,
        {
            "mercari": {
                "category_id": "303",
                "fields": [
                    {
                        "path": SHOEBOX,
                        "label": "Will your item fit in a shoebox?",
                        "required": True,
                        "options": ["Yes", "No"],
                        "option_values": {"Yes": "yes", "No": "no"},
                        "selection_only": True,
                    }
                ],
            }
        },
        ["mercari"],
    )

    without_default = validate_listing(copy.deepcopy(listing), 1, selected_marketplaces=["mercari"])
    assert any(err["field"] == SHOEBOX for err in without_default.errors), without_default.errors

    user_settings.set_listing_field_default(SHOEBOX, "yes")
    result = validate_listing(listing, 1, selected_marketplaces=["mercari"])

    assert not any(err["field"] == SHOEBOX for err in result.errors), result.errors
    assert result.valid, result.errors
    # Default answers readiness without writing onto the saved listing.
    assert "package" not in (listing.get("mercari_specifics") or {})
