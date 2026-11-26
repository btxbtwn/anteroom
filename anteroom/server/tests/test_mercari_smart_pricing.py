"""Mercari Smart Pricing minimum must stay below the listing price (#2244)."""
from __future__ import annotations

from anteroom.services import auto_relist, direct_preflight, listing_readiness, mercari_form
from anteroom.services.chrome_listing import mercari_requirements
from anteroom.services.direct_preflight import Context

BASE = {
    "title": "Cascade Blue L Floral Embroidered Button-Front Shirt Tan",
    "description": "Floral embroidered shirt.",
    "direct_category_ids": {"mercari": "165"},
    "weight_oz": 14,
    "mercari_specifics": {
        "shipsFrom": "70115",
        "shippingClassId": "2552",
        "package": {"fitsShoebox": "yes"},
    },
}


def _snapshot(price: float | str, smart: dict | bool | None = None, *, mercari_price: float | None = None) -> dict:
    specifics = {**BASE["mercari_specifics"]}
    if smart is not None:
        specifics["smartPricing"] = smart
    out = {**BASE, "price": price, "mercari_specifics": specifics}
    if mercari_price is not None:
        specifics["price"] = mercari_price
        out["seller_fields"] = ["mercari_specifics.price"]
    return out


def _smart_gaps(snapshot: dict) -> list[dict]:
    return [gap for gap in mercari_requirements(snapshot, Context())
            if gap["field"].startswith("mercari_specifics.smartPricing")]


def test_minimum_at_or_above_listing_price_blocks_list_and_shows_in_review():
    # Production 2026-11-25 (listing 14e97643a0a6): price $11, Smart Pricing minimum $11.
    for snapshot in (
        _snapshot(11, {"enabled": True, "minimumPrice": 11}),
        _snapshot(9.9, {"enabled": True, "minimumPrice": 11}, mercari_price=11.0),
        _snapshot(11, {"enabled": True, "minimumPrice": 12}),
        # Mercari rounds whole dollars (Math.round): $10.60 sends $11.
        _snapshot(11, {"enabled": True, "minimumPrice": 10.6}),
    ):
        gaps = _smart_gaps(snapshot)
        assert len(gaps) == 1, snapshot
        assert gaps[0]["blocking"] is True
        assert gaps[0]["field"] == "mercari_specifics.smartPricing.minimumPrice"
        assert "$10" in gaps[0]["message"]

    missing = listing_readiness.missing(direct_preflight.gaps(_snapshot(11, {"enabled": True, "minimumPrice": 11}), "mercari"))
    assert any(item["field"] == "mercari_specifics.smartPricing.minimumPrice" and item["label"] == "Minimum price"
               for item in missing)


def test_missing_or_zero_minimum_or_one_dollar_price_blocks_list():
    for snapshot in (
        _snapshot(11, {"enabled": True}),
        _snapshot(11, {"enabled": True, "minimumPrice": ""}),
        _snapshot(11, {"enabled": True, "minimumPrice": 0}),
        _snapshot(1, {"enabled": True, "minimumPrice": 1}),
    ):
        gaps = _smart_gaps(snapshot)
        assert len(gaps) == 1, snapshot
        assert gaps[0]["blocking"] is True
        assert gaps[0]["field"] == "mercari_specifics.smartPricing.minimumPrice"


def test_valid_minimum_or_disabled_smart_pricing_passes():
    for snapshot in (
        _snapshot(11, {"enabled": True, "minimumPrice": 10}),
        _snapshot(11, {"enabled": False, "minimumPrice": 11}),
        _snapshot(11, {}),
        _snapshot(11, None),
    ):
        assert _smart_gaps(snapshot) == [], snapshot


def test_mercari_form_defaults_smart_pricing_below_price_on_low_priced_items():
    for price, expected_enabled, expected_min in ((1, False, ""), (2, True, 1), (3, True, 2), (4, True, 3)):
        fields = {row["path"]: row for row in mercari_form.form({"price": price})["fields"]}
        assert fields["mercari_specifics.smartPricing.enabled"]["value"] is expected_enabled
        assert fields["mercari_specifics.smartPricing.minimumPrice"]["value"] == expected_min


def test_auto_relist_disables_mercari_smart_pricing_when_price_reaches_floor():
    listing = _snapshot(13, {"enabled": True, "minimumPrice": 11})
    at_floor = auto_relist._snapshot_with_relist_price(listing, "mercari", 11.0)
    assert at_floor["mercari_specifics"]["smartPricing"] == {"enabled": False, "minimumPrice": 11}
    assert _smart_gaps(at_floor) == []

    above_floor = auto_relist._snapshot_with_relist_price(listing, "mercari", 12.0)
    assert above_floor["mercari_specifics"]["smartPricing"] == {"enabled": True, "minimumPrice": 11}
    assert _smart_gaps(above_floor) == []


def test_auto_relist_on_another_marketplace_disables_unlisted_mercari_smart_pricing_at_floor():
    listing = _snapshot(13, {"enabled": True, "minimumPrice": 11})
    unlisted = auto_relist._general_with_relist_price(listing, "ebay", 11.0, live_elsewhere=set())
    assert unlisted["mercari_specifics"]["smartPricing"] == {"enabled": False, "minimumPrice": 11}

    # When Mercari is live elsewhere, its price is held at $13 so Smart Pricing stays untouched.
    live_mercari = auto_relist._general_with_relist_price(listing, "ebay", 11.0, live_elsewhere={"mercari"})
    assert live_mercari["mercari_specifics"]["smartPricing"] == {"enabled": True, "minimumPrice": 11}
