from __future__ import annotations

import unittest

from vendoo_studio.models.facebook_shipping import package_weight_label
from vendoo_studio.services.vendoo_marketplace_fields import vinted_measurements

CARRIER = "USPS_GA:USPS Ground Advantage"
DESCRIPTION = (
    "Faded Harry Potter graphic tee.\n\n"
    'Measurements: Pit to pit: 20.5"; Length: 28"; Sleeve: 8"'
)


def _spec(name, options=()):
    return {
        "id": name,
        "display": name,
        "rules": {"fieldType": "select", "fieldOptions": {
            "minValues": 0, "maxValues": 1, "selectionMode": "SelectionOnly",
        }},
        "options": {str(i): {"id": code, "display": label} for i, (code, label) in enumerate(options)},
    }


def _seller_item(*, ounces="8", carrier=CARRIER, tier="0.5-1 lbs", package_size="2"):
    """A draft the seller finished by hand on Vendoo's Facebook and Vinted forms."""
    return {
        "generalDetails": {"weight": {"pounds": "0", "ounces": ounces}},
        "listings": {
            "facebook": {
                "overrides": {"categoryV2": {"id": "fb1"}},
                "marketplaceSpecifics": {"packageWeightLabel": tier, "carrier": carrier},
            },
            "vinted": {
                "overrides": {"categoryV2": {"id": "77"}},
                "categorySpecifics": {"77_packageSizeId": package_size},
            },
        },
    }


def _listing(**extra):
    return {
        "title": "Harry Potter Tee",
        "description": DESCRIPTION,
        "weight_lb": 0,
        "weight_oz": 8,
        "marketplace_category_objects": {"facebook": {"id": "fb1"}, "vinted": {"id": "77"}},
        **extra,
    }


class PackageWeightLabelTest(unittest.TestCase):
    def test_tiers_follow_the_weight(self):
        for ounces, label in ((4, "Under 0.5 lbs"), (7, "Under 0.5 lbs"), (8, "0.5-1 lbs"),
                              (16, "1-2 lbs"), (40, "2-5 lbs"), (159, "5-10 lbs"), (200, "")):
            with self.subTest(ounces=ounces):
                self.assertEqual(package_weight_label({"pounds": "0", "ounces": str(ounces)}), label)

    def test_a_weightless_listing_ships_as_half_a_pound(self):
        self.assertEqual(package_weight_label(None), "0.5-1 lbs")


class VintedPackageTest(unittest.TestCase):
    def test_measurements_come_from_the_sellers_measurements_line(self):
        self.assertEqual(vinted_measurements({"description": DESCRIPTION}), {"width": "20.5", "length": "28"})
        self.assertEqual(vinted_measurements({"description": "Measurements: See photos"}), {})


if __name__ == "__main__":
    unittest.main()
