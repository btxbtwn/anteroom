from __future__ import annotations


import pytest

from vendoo_studio.services.ebay_fields import ebay_size_for_type
from vendoo_studio.services.listing_generate import propagate_general_size
from vendoo_studio.services.vendoo_item_build import build_vendoo_item
from vendoo_studio.services.vendoo_specifics import FieldSpec










def test_fabric_type_resolves_single_option_and_material_keeps_chips():
    item, _ = build_vendoo_item({
        "marketplace_category_ids": {"ebay": "53159"},
        "ebay_specifics": {"fabricType": ["Polyester"], "material": ["Polyester", "Spandex"]},
    }, specifics={"ebay": {
        "Fabric Type": FieldSpec("Fabric Type", options={"poly": "Polyester"}),
        "Material": FieldSpec("Material", multi=True, options={"poly": "Polyester", "span": "Spandex"}),
    }})
    assert item["listings"]["ebay"]["categorySpecifics"] == {
        "53159_Fabric Type": "poly", "53159_Material": ["poly", "span"],
    }






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










def test_learned_draft_shape_also_preserves_petite_size():
    item, _ = build_vendoo_item(petite_listing(), schema={"aspects": {"ebay": {
        "Size": "scalar", "Size Type": "scalar",
    }}})
    assert item["listings"]["ebay"]["categorySpecifics"]["53159_Size"] == "PL"
