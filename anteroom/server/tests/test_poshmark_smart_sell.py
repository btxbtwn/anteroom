"""Smart Sell's Lowest Price must cover Poshmark's fee, or Poshmark refuses the listing (#1988)."""
from __future__ import annotations

from anteroom.services.chrome_listing import poshmark_requirements
from anteroom.services.direct_preflight import Context
from anteroom.services.vendoo_marketplace_fields import poshmark_smart_sell

BASE = {"title": "Tee", "description": "Soft cotton", "direct_category_ids": {"poshmark": "a/b/c"}}


def _smart_gaps(snapshot: dict) -> list[dict]:
    return [gap for gap in poshmark_requirements({**BASE, **snapshot}, Context())
            if gap["field"].startswith("poshmark_specifics.smartSell")]


def test_cheap_item_lists_with_smart_sell_off():
    # $5 - $4 = $1, which does not cover the $2.95 fee.
    assert poshmark_smart_sell(5) == {"enabled": False, "minPrice": ""}
    assert poshmark_smart_sell("6") == {"enabled": False, "minPrice": ""}


def test_floor_that_covers_the_fee_keeps_smart_sell_on():
    assert poshmark_smart_sell(8) == {"enabled": True, "minPrice": "4"}
    assert poshmark_smart_sell(24) == {"enabled": True, "minPrice": "20"}


def test_default_floor_on_a_cheap_item_does_not_hold_list():
    snapshot = {"price": "5", "form": {"specifics": {"smartSell": poshmark_smart_sell(5)}}}
    assert _smart_gaps(snapshot) == []


def test_lowest_price_below_the_fee_holds_list_with_poshmark_reason():
    for snapshot in (
        {"price": "5", "poshmark_specifics": {"smartSell": {"enabled": True, "minimumPrice": 1}}},
        {"price": "5", "form": {"specifics": {"smartSell": {"enabled": True, "minPrice": "1"}}}},
    ):
        gaps = _smart_gaps(snapshot)
        assert len(gaps) == 1, snapshot
        assert gaps[0]["blocking"] is True
        assert gaps[0]["field"] == "poshmark_specifics.smartSell.minimumPrice"
        assert "$3" in gaps[0]["message"]


def test_seller_turning_smart_sell_off_wins_over_the_form_default():
    snapshot = {"price": "5", "poshmark_specifics": {"smartSell": {"enabled": False}},
                "form": {"specifics": {"smartSell": {"enabled": True, "minPrice": "1"}}}}
    assert _smart_gaps(snapshot) == []


def test_lowest_price_that_covers_the_fee_lists():
    assert _smart_gaps({"price": "24", "poshmark_specifics": {"smartSell": {"enabled": True, "minimumPrice": 20}}}) == []


def _with_discount(snapshot: dict, amount: float) -> dict:
    """A listing whose seller chose a shipping discount their saved Poshmark form knows the amount of."""
    specifics = {**snapshot.get("poshmark_specifics", {}), "shippingDiscountId": "seller-option"}
    return {**snapshot, "poshmark_specifics": specifics,
            "_native_forms": {"poshmark": {"fields": [{"path": "poshmark_specifics.shippingDiscountId",
                                                       "option_amounts": {"seller-option": amount}}]}}}


def test_lowest_price_must_also_cover_the_shipping_discount():
    # $4 covers the $2.95 fee alone, but not with a $1.50 shipping discount on top.
    snapshot = {"price": "10", "poshmark_specifics": {"smartSell": {"enabled": True, "minimumPrice": 4}}}
    assert _smart_gaps(snapshot) == []
    gaps = _smart_gaps(_with_discount(snapshot, 1.5))
    assert len(gaps) == 1
    assert "$5" in gaps[0]["message"] and "shipping discount" in gaps[0]["message"]
    raised = {"price": "10", "poshmark_specifics": {"smartSell": {"enabled": True, "minimumPrice": 5}}}
    assert _smart_gaps(_with_discount(raised, 1.5)) == []


def test_unknown_discount_amount_checks_the_fee_alone():
    snapshot = {"price": "10", "poshmark_specifics": {"smartSell": {"enabled": True, "minimumPrice": 4},
                                                      "shippingDiscountId": "seller-option"}}
    assert _smart_gaps(snapshot) == []
