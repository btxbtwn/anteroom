from __future__ import annotations

import unittest

from vendoo_studio.services.vendoo_categories import (
    pick_category_hit,
    pick_mapped_category,
)
from vendoo_studio.services.vendoo_encodings import (
    encode_field,
    observe_item_schema,
)
from vendoo_studio.services.vendoo_item_shape import (
    default_listing_section,
)


# Shaped like what GET /api/item/{id} returns: coded values carrying a label.
PROBED_ITEMS = [
    {
        "generalDetails": {
            "title": "Levi's 501",
            "condition": {"value": "v_pre_owned_good", "displayName": "Pre-Owned - Good"},
            "primaryColor": {"value": "v_blue", "displayName": "Blue"},
            "brand": "Levi's",
        }
    },
    {
        "generalDetails": {
            "condition": {"value": "v_new_with_tags", "displayName": "New With Tags/Box"},
            "primaryColor": {"value": "v_black", "displayName": "Black"},
            "secondaryColor": {"value": "v_white", "displayName": "White"},
        }
    },
]

IMAGES = [{"version": 3, "id": "images/u1/a.jpg", "originalMaxDimension": 1600}]

LISTING = {
    "title": "Levi's 501 Straight Jeans",
    "description": "Classic fit.",
    "price": 48.0,
    "cost": 20,
    "quantity": 1,
    "brand": "Levi's",
    "condition": "Pre-Owned - Good",
    "primaryColor": "Blue",
    "category_path": "Clothing > Men > Jeans",
    "size": "32x34",
    "sizeType": "Regular",
    "sku": "LV-501",
    "tags": ["denim", "vintage"],
    "labels": ["shelf-a"],
    "weight_lb": 1,
    "weight_oz": 8,
    "package_dimensions_in": "13x10x3",
    "internal_notes": "bin 4",
    "ebay_specifics": {"department": "Men", "fit": "Straight", "pattern": None, "shippingService": "USPSGround"},
    "poshmark_specifics": {"categoryPath": ["Men", "Jeans", "Straight"], "originalPrice": 90},
    "mercari_specifics": {"categoryPath": ["Men", "Jeans"], "shippingLabel": "USPS Ground Advantage", "smartPricing": True},
    "depop_specifics": {"style": ["Streetwear"], "source": "Preloved"},
    # Vintage, so Etsy allows it and Send fills its form.
    "etsy_specifics": {
        "who_made": "someone_else", "when_made": "1990s (Vintage)",
        "materials": ["denim"], "category_specifics": {"Pattern": "Solid"},
    },
}


def _spec(name, *, options=(), min_values=0, max_values=1, free_text=False):
    """One entry shaped like Vendoo's category-specifics payload."""
    built = {}
    for index, option in enumerate(options):
        code, display = option if isinstance(option, tuple) else (option, option)
        built[str(index)] = {"id": code, "display": display}
    return {
        "id": name,
        "display": name,
        "rules": {
            "fieldType": "select",
            "fieldOptions": {
                "minValues": min_values,
                "maxValues": max_values,
                "selectionMode": "FreeText" if free_text else "SelectionOnly",
            },
        },
        "options": built,
    }


class PickCategoryHitTest(unittest.TestCase):
    def test_prefers_exact_path_leaf(self):
        hits = [
            {"id": "wrong", "is_leaf": True, "path": "Women > Tops > Tees"},
            {"id": "right", "is_leaf": True, "path": "Women > Tops > Blouses"},
            {"id": "parent", "is_leaf": False, "path": "Women > Tops"},
        ]
        hit = pick_category_hit(hits, "Women > Tops > Blouses")
        self.assertEqual(hit["id"], "right")

    def test_falls_back_to_matching_leaf_name(self):
        hits = [
            {"id": "a", "is_leaf": True, "path": "Clothing > Women > Tops"},
            {"id": "b", "is_leaf": True, "path": "Other > Blouses"},
        ]
        hit = pick_category_hit(hits, "Women > Tops > Blouses")
        self.assertEqual(hit["id"], "b")


class ObserveSchemaTest(unittest.TestCase):
    def test_learns_label_to_code_pairs(self):
        schema = observe_item_schema(PROBED_ITEMS)
        self.assertEqual(schema["item_count"], 2)
        condition = schema["fields"]["condition"]
        self.assertEqual(condition["shape"], "object")
        self.assertEqual(condition["labels"]["pre owned good"], "v_pre_owned_good")
        self.assertEqual(condition["labels"]["new with tags box"], "v_new_with_tags")

    def test_bare_code_teaches_its_own_label(self):
        schema = observe_item_schema([{"generalDetails": {"condition": "v_pre_owned_fair"}}])
        self.assertEqual(encode_field(schema, "condition", "Pre Owned Fair"), ("v_pre_owned_fair", True))

    def test_ignores_non_dict_items(self):
        self.assertEqual(observe_item_schema(["nope", None])["item_count"], 0)


class DefaultSectionTest(unittest.TestCase):
    def test_matches_vendoo_factory_shape(self):
        section = default_listing_section("poshmark")
        self.assertEqual(section["marketplaceID"], "poshmark")
        self.assertEqual(section["type"], "listing")
        self.assertEqual(section["status"], {"notListed": True})
        self.assertEqual(section["overrides"]["quantity"], "1")
        self.assertEqual(section["overrides"]["weight"], {"pounds": "0", "ounces": "0"})
        self.assertEqual(section["marketplaceSpecifics"], {
            "originalPrice": "",
            "smartSell": {"enabled": False, "minPrice": ""},
        })
        self.assertEqual(section["sales"], [])

    def test_ebay_and_etsy_defaults(self):
        self.assertEqual(default_listing_section("ebay")["marketplaceSpecifics"]["pricingFormat"], "FixedPriceItem")
        self.assertEqual(default_listing_section("etsy")["marketplaceSpecifics"]["quantity"], 1)
        self.assertEqual(default_listing_section("depop")["overrides"], {"quantity": "1"})
        self.assertIn("listedID", default_listing_section("shopify"))


class RoundTripTest(unittest.TestCase):
    """The serializer must survive a trip through Studio's own reader."""


class GeneralFormFieldsTest(unittest.TestCase):
    """The general form feeds every marketplace, so its own fields must be
    stored the way Vendoo stores them. Shapes taken from real saved items."""

    LISTING = {
        "title": "Cat Graphic Tee",
        "size": "XL",
        "sizeType": "Regular",
        "primaryColor": "Beige",
        "secondaryColor": "Pink",
        "category_id": "clothing_shoes__accessories__women__women's_clothing__tops",
        "category_path": "Clothing, Shoes & Accessories > Women > Women's Clothing > Tops",
    }


    def test_size_carries_the_category_it_belongs_to(self):
        item, _ = self.build()
        size = item["generalDetails"]["size"]
        self.assertEqual(size["option"], {"label": "XL", "value": "XL"})
        self.assertEqual(size["scale"], {"label": "Regular", "value": "Regular"})
        # Without categoryId the form has no scale to read the option against.
        self.assertEqual(size["categoryId"], self.LISTING["category_id"])

    def test_colours_use_vendoos_vocabulary_not_the_words(self):
        item, unresolved = self.build()
        general = item["generalDetails"]
        self.assertEqual(general["primaryColor"], "v_Beige")
        self.assertEqual(general["secondaryColor"], "v_Pink")
        # A colour Vendoo ships is not something to report as unencodable.
        self.assertEqual([r for r in unresolved if "Color" in r["field"]], [])

    def test_a_colour_vendoo_does_not_ship_is_reported(self):
        _item, unresolved = self.build(primaryColor="Chartreuse")
        self.assertEqual(
            [r["field"] for r in unresolved if r["value"] == "Chartreuse"], ["primaryColor"]
        )

    def test_size_without_a_category_omits_the_key(self):
        item, _ = self.build(category_id="", category_path="")
        self.assertNotIn("categoryId", item["generalDetails"]["size"])


class MappedCategoryTest(unittest.TestCase):
    """Vendoo's mapper offers alternates; its first answer is not always best."""

    GENERAL = {"displayPath": [
        "Clothing, Shoes & Accessories", "Kids", "Girls",
        "Girls' Clothing (Sizes 4 & Up)", "Tops, Shirts & T-Shirts",
    ]}

    def hit(self, cid, labels):
        return {"id": cid, "all_category_label": labels}

    def test_the_wrong_gender_loses_to_the_right_one(self):
        # Real answer for a girls' tee: eBay matched Boys', with Girls' among
        # the alternates. Boys' Department offers no "Girls" at all.
        match = self.hit("260966", ["Clothing, Shoes & Accessories", "Kids", "Boys",
                                    "Boys' Clothing (Sizes 4 & Up)", "Tops, Shirts & T-Shirts"])
        recs = [self.hit("260965", ["Clothing, Shoes & Accessories", "Kids", "Girls",
                                    "Girls' Clothing (Sizes 4 & Up)", "Tops, Shirts & T-Shirts"])]
        self.assertEqual(pick_mapped_category(self.GENERAL, match, recs)["id"], "260965")

    def test_the_wrong_garment_loses_to_the_right_one(self):
        # Etsy matched Tanks, whose Sleeve length offers only "Sleeveless".
        match = self.hit("11142", ["Clothing", "Girls' Clothing", "Tops & Tees", "Tanks"])
        recs = [self.hit("11143", ["Clothing", "Girls' Clothing", "Tops & Tees", "T-shirts"])]
        self.assertEqual(pick_mapped_category(self.GENERAL, match, recs)["id"], "11143")

    def test_tank_tops_does_not_beat_tees_for_womens_tops(self):
        """Parent-echo scoring used to give Tank Tops a leaf hit on bare Tops."""
        general = {"displayPath": [
            "Clothing, Shoes & Accessories", "Women", "Women's Clothing", "Tops",
        ]}
        match = self.hit("tank", ["Women", "Tops", "Tank Tops"])
        recs = [self.hit("tee", ["Women", "Tops", "Tees - Short Sleeve"])]
        self.assertEqual(pick_mapped_category(general, match, recs)["id"], "tee")

    def test_tunics_does_not_beat_tees_for_womens_tops(self):
        """Vendoo often maps bare Women's Tops to Etsy Tunics; prefer a tee leaf."""
        general = {"displayPath": [
            "Clothing, Shoes & Accessories", "Women", "Women's Clothing", "Tops",
        ]}
        match = self.hit("tunic", [
            "Clothing", "Women's Clothing", "Tops & Tees", "Tunics",
        ])
        recs = [self.hit("tee", [
            "Clothing", "Women's Clothing", "Tops & Tees", "T-shirts",
        ])]
        self.assertEqual(pick_mapped_category(general, match, recs)["id"], "tee")

    def test_real_tank_match_stands_when_general_names_tank(self):
        general = {"displayPath": [
            "Clothing, Shoes & Accessories", "Women", "Women's Clothing", "Tops", "Tank Tops",
        ]}
        match = self.hit("tank", ["Women", "Tops", "Tank Tops"])
        recs = [self.hit("tee", ["Women", "Tops", "Tees - Short Sleeve"])]
        self.assertEqual(pick_mapped_category(general, match, recs)["id"], "tank")

    def test_the_match_keeps_a_tie(self):
        """Only override Vendoo when something genuinely fits better."""
        match = self.hit("a", ["Clothing", "Girls' Clothing", "Tops & Tees", "T-shirts"])
        recs = [self.hit("b", ["Clothing", "Girls' Clothing", "Tops & Tees", "T-shirts"])]
        self.assertEqual(pick_mapped_category(self.GENERAL, match, recs)["id"], "a")

    def test_no_answer_at_all_is_none(self):
        self.assertIsNone(pick_mapped_category(self.GENERAL, None, []))

    def test_without_a_general_path_the_match_stands(self):
        match = self.hit("a", ["Anything"])
        self.assertEqual(pick_mapped_category({}, match, [self.hit("b", ["Other"])])["id"], "a")


if __name__ == "__main__":
    unittest.main()
