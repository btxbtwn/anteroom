from __future__ import annotations


import pytest

from vendoo_studio.services.ebay_fields import ebay_size_for_type
from vendoo_studio.services.listing_generate import propagate_general_size
from vendoo_studio.services.vendoo_specifics import FieldSpec


@pytest.mark.parametrize(("size", "expected"), [
    ("L", "PL"), ("LP", "PL"), ("PL", "PL"), ("S", "PS"),
    ("XL", "PXL"), ("12", "12P"), ("12P", "12P"),
])
def test_ebay_petite_size_mapping(size, expected):
    assert ebay_size_for_type(size, "Petites") == expected
    assert ebay_size_for_type(size, "Regular") == size


def petite_listing():
    return {"size": "L", "marketplace_category_ids": {"ebay": "53159"},
            "ebay_specifics": {"sizeType": "Petites", "size": "L"}}


def petite_specs():
    return {"ebay": {
        "Size": FieldSpec("Size", required=True, selection_only=True, options={"L": "L", "PL": "PL"}),
        "Size Type": FieldSpec("Size Type", required=True, selection_only=True, options={"Petites": "Petites"}),
    }}


def test_size_sync_keeps_petite_pair_without_changing_general_size():
    listing = petite_listing()
    assert propagate_general_size(listing)
    assert listing["size"] == "L"
    assert listing["ebay_specifics"] == {"sizeType": "Petites", "size": "PL"}
    assert not propagate_general_size(listing)
