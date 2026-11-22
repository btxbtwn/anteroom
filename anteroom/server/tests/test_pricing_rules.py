"""Fee-aware pricing rules: settings, the prices that follow them, and drift warnings."""
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from anteroom.main import app
from anteroom.services import pricing_rules
from anteroom.services.pricing_rules import (
    PRICING_RULES_KEY,
    apply_rule,
    apply_rules_to_listing,
    default_rule,
    price_drift,
    round_price,
    rule_price,
)
from anteroom.services.seller_overrides import (
    apply_price_policy,
    prepare_user_listing,
    price_for,
    sync_unlocked_prices,
)
from anteroom.services.user_settings import update_settings


@pytest.fixture(autouse=True)
def _no_rules():
    update_settings(lambda payload: payload.pop(PRICING_RULES_KEY, None))
    yield
    update_settings(lambda payload: payload.pop(PRICING_RULES_KEY, None))


def rules(anchor="general", **overrides):
    config = pricing_rules.normalize({"enabled": True, "anchor": anchor})
    for market, rule in overrides.items():
        config["rules"][market] = {**config["rules"][market], **rule}
    return config


def turn_on(anchor="general", **overrides):
    config = rules(anchor, **overrides)
    return pricing_rules.set_pricing_rules(enabled=True, anchor=anchor, rules=config["rules"])


def test_default_rules_offset_typical_fees_from_the_item_price():
    ebay = default_rule("ebay")
    assert ebay["percent"] == pytest.approx(15.7, abs=0.05)
    assert ebay["fixed"] == pytest.approx(0.46, abs=0.01)
    # Poshmark only takes whole dollars, whatever its rule says.
    assert default_rule("poshmark") == {"percent": 25.0, "fixed": 0.0, "rounding": "whole"}
    # Seller keeps about the same after fees: 25 on General, Mercari priced to net 25.
    mercari = apply_rule(Decimal(25), default_rule("mercari"), "mercari")
    assert mercari is not None
    net = mercari * Decimal("0.9") - Decimal("0.50")
    assert abs(net - 25) < Decimal("0.02")


def test_default_rules_from_a_marketplace_anchor_are_relative_to_its_fees():
    # Priced from Poshmark (20%), Depop's lower fee means a lower price.
    assert default_rule("depop", "poshmark")["percent"] < 0
    assert default_rule("poshmark", "poshmark")["percent"] == 0


@pytest.mark.parametrize("amount,rounding,market,expected", [
    ("25.00", "none", "ebay", "25.00"),
    ("25.004", "none", "ebay", "25.00"),
    ("25.00", "99", "ebay", "25.99"),
    ("25.99", "99", "ebay", "25.99"),
    ("25.10", "99", "ebay", "25.99"),
    ("25.10", "whole", "ebay", "26"),
    ("25.00", "whole", "ebay", "25"),
    ("25.10", "99", "poshmark", "26"),
    ("25.10", "none", "poshmark", "26"),
])
def test_endings_round_up(amount, rounding, market, expected):
    assert round_price(Decimal(amount), rounding, market) == Decimal(expected)


def test_a_rule_that_prices_at_or_below_zero_gives_no_price():
    assert apply_rule(Decimal(5), {"percent": 0, "fixed": -10, "rounding": "none"}, "ebay") is None


def test_settings_round_trip_validate_and_reset():
    client = TestClient(app)
    off = client.get("/api/settings/pricing-rules").json()
    assert off["enabled"] is False and off["anchor"] == "general"
    assert set(off["rules"]) == {"ebay", "poshmark", "mercari", "depop", "etsy"}
    assert off["rules"] == off["defaults"]
    custom = {"enabled": True, "rules": {"ebay": {"percent": 10, "fixed": 1, "rounding": "99"},
                                         "poshmark": {"percent": 20, "fixed": 0, "rounding": "none"}}}
    saved = client.put("/api/settings/pricing-rules", json=custom)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["enabled"] is True
    assert body["rules"]["ebay"] == {"percent": 10.0, "fixed": 1.0, "rounding": "99"}
    # Poshmark's whole-dollar rule is enforced on save.
    assert body["rules"]["poshmark"]["rounding"] == "whole"
    assert client.get("/api/settings/pricing-rules").json()["rules"]["ebay"]["percent"] == 10.0
    assert client.put("/api/settings/pricing-rules", json={"rules": {"ebay": {"percent": 900, "fixed": 0}}}).status_code == 422
    assert client.put("/api/settings/pricing-rules", json={"anchor": "vinted"}).status_code == 422
    reset = client.put("/api/settings/pricing-rules", json={"anchor": "poshmark", "reset": True}).json()
    assert reset["anchor"] == "poshmark"
    assert reset["rules"] == pricing_rules.default_rules("poshmark") == reset["defaults"]


def test_rules_off_keeps_every_price_on_the_item_price():
    listing = {"price": 25, "ebay_specifics": {"price": 25}, "poshmark_specifics": {"poshmarkPrice": 25}}
    assert price_for(listing, "ebay") == 25
    assert not sync_unlocked_prices(listing)


def test_following_prices_take_the_rules_price_and_overrides_stay():
    turn_on(ebay={"percent": 10, "fixed": 0, "rounding": "none"}, poshmark={"percent": 20, "fixed": 0})
    listing = {"price": 25, "ebay_specifics": {"price": 25}, "poshmark_specifics": {"poshmarkPrice": 25},
               "mercari_specifics": {"price": 40}, "seller_fields": ["mercari_specifics.price"]}
    assert price_for(listing, "ebay") == 27.5
    assert price_for(listing, "poshmark") == 30
    assert price_for(listing, "mercari") == 40
    assert sync_unlocked_prices(listing)
    assert listing["ebay_specifics"]["price"] == 27.5
    assert listing["poshmark_specifics"]["poshmarkPrice"] == 30
    assert listing["mercari_specifics"]["price"] == 40


def test_a_general_price_edit_moves_following_prices_through_the_rules():
    turn_on(ebay={"percent": 10, "fixed": 0, "rounding": "whole"})
    previous = {"price": 20, "ebay_specifics": {"price": 22}, "mercari_specifics": {"price": 20}}
    submitted = {**previous, "price": 30, "ebay_specifics": {"price": 22}, "mercari_specifics": {"price": 20}}
    apply_price_policy(previous, submitted)
    assert submitted["ebay_specifics"]["price"] == 33
    # Mercari's default rule, not the bare item price.
    assert submitted["mercari_specifics"]["price"] == price_for({"price": 30}, "mercari")
    assert "ebay_specifics.price" not in submitted.get("seller_fields", [])


def test_turning_rules_on_does_not_lock_prices_that_followed_the_item_price():
    previous = {"price": 20, "ebay_specifics": {"price": 20}}
    turn_on(ebay={"percent": 10, "fixed": 0, "rounding": "none"})
    submitted = prepare_user_listing(previous, {**previous, "title": "Edited", "ebay_specifics": {"price": 20}})
    assert submitted["ebay_specifics"]["price"] == 22
    assert "ebay_specifics.price" not in submitted.get("seller_fields", [])


def test_a_typed_marketplace_price_stays_and_a_rules_price_unlocks():
    turn_on(ebay={"percent": 10, "fixed": 0, "rounding": "none"})
    previous = {"price": 20, "ebay_specifics": {"price": 22}}
    typed = prepare_user_listing(previous, {"price": 20, "ebay_specifics": {"price": 19}})
    assert typed["ebay_specifics"]["price"] == 19
    assert "ebay_specifics.price" in typed["seller_fields"]
    back = prepare_user_listing(typed, {**typed, "ebay_specifics": {"price": 22}})
    assert "ebay_specifics.price" not in back.get("seller_fields", [])


def test_a_marketplace_anchor_prices_the_others_from_its_own_price():
    config = rules("ebay", mercari={"percent": -10, "fixed": 0, "rounding": "none"})
    listing = {"price": 20, "ebay_specifics": {"price": 30}, "seller_fields": ["ebay_specifics.price"]}
    assert rule_price(listing, "ebay", config) == Decimal("30.00")
    assert rule_price(listing, "mercari", config) == Decimal("27.00")
    # Until eBay has its own price, the anchor is the item price.
    assert rule_price({"price": 20}, "mercari", config) == Decimal("18.00")


def test_drift_lists_overrides_and_live_prices_off_the_rules():
    config = rules(ebay={"percent": 10, "fixed": 0, "rounding": "none"})
    listing = {"price": 20, "ebay_specifics": {"price": 25}, "seller_fields": ["ebay_specifics.price"]}
    drift = price_drift(listing, config, {"mercari": 15})
    assert {"marketplace": "ebay", "expected": 22, "price": 25, "source": "override"} in drift
    assert any(row["marketplace"] == "mercari" and row["source"] == "live" for row in drift)
    assert price_drift(listing, None) == []
    on_rules = {"price": 20, "ebay_specifics": {"price": 22}}
    assert price_drift(on_rules, config, {"ebay": 22}) == []


def test_apply_rules_releases_overrides_but_keeps_the_anchor():
    config = rules("ebay", mercari={"percent": 10, "fixed": 0, "rounding": "none"})
    listing = {"price": 20, "ebay_specifics": {"price": 30}, "mercari_specifics": {"price": 50},
               "seller_fields": ["ebay_specifics.price", "mercari_specifics.price", "title"]}
    updated, changed = apply_rules_to_listing(listing, config)
    assert changed == ["mercari"]
    assert updated["mercari_specifics"]["price"] == 33
    assert updated["ebay_specifics"]["price"] == 30
    assert updated["seller_fields"] == ["ebay_specifics.price", "title"]
    assert listing["mercari_specifics"]["price"] == 50


def test_apply_route_needs_rules_on_and_saves_nothing():
    client = TestClient(app)
    listing = {"price": 20, "ebay_specifics": {"price": 30}, "seller_fields": ["ebay_specifics.price"]}
    assert client.post("/api/settings/pricing-rules/apply", json={"listing": listing}).status_code == 400
    turn_on(ebay={"percent": 0, "fixed": 2, "rounding": "none"})
    result = client.post("/api/settings/pricing-rules/apply", json={"listing": listing})
    assert result.status_code == 200, result.text
    assert result.json()["listing"]["ebay_specifics"]["price"] == 22
    assert result.json()["changed"] == ["ebay"]


def test_rule_changes_and_disabling_keep_derived_prices_following():
    turn_on(ebay={"percent": 10, "fixed": 0, "rounding": "none"})
    previous = {"price": 20, "ebay_specifics": {"price": 20}, "etsy_specifics": {"price": 33},
                "seller_fields": ["etsy_specifics.price"]}
    assert sync_unlocked_prices(previous)
    assert previous["ebay_specifics"]["price"] == 22
    turn_on(ebay={"percent": 0, "fixed": -50, "rounding": "none"})
    previous = prepare_user_listing(previous, {**previous, "title": "No valid rule price"})
    assert previous["ebay_specifics"]["price"] == 22
    assert "ebay_specifics.price" not in previous["seller_fields"]
    turn_on(ebay={"percent": 20, "fixed": 0, "rounding": "none"})
    current = prepare_user_listing(previous, {**previous, "title": "Edited"})
    assert current["ebay_specifics"]["price"] == 24
    assert current["etsy_specifics"]["price"] == 33
    pricing_rules.set_pricing_rules(enabled=False)
    current = prepare_user_listing(current, {**current, "price": 25})
    assert current["ebay_specifics"]["price"] == 25
    assert "ebay_specifics.price" not in current["seller_fields"]
    assert "etsy_specifics.price" in current["seller_fields"]


def test_editing_the_anchor_keeps_its_lock_until_it_matches_general():
    turn_on("ebay", mercari={"percent": 10, "fixed": 0, "rounding": "none"})
    previous = {"price": 20, "ebay_specifics": {"price": 30}, "mercari_specifics": {"price": 30},
                "seller_fields": ["ebay_specifics.price"]}
    sync_unlocked_prices(previous)
    edited = prepare_user_listing(previous, {**previous, "ebay_specifics": {"price": 40}})
    assert edited["seller_fields"] == ["ebay_specifics.price"]
    assert price_for(edited, "ebay") == 40
    assert edited["mercari_specifics"]["price"] == 44
    reset = prepare_user_listing(edited, {**edited, "ebay_specifics": {"price": 20}})
    assert "ebay_specifics.price" not in reset.get("seller_fields", [])
    assert reset["mercari_specifics"]["price"] == 22


def test_unlocked_poshmark_anchor_uses_whole_dollars():
    turn_on("poshmark", ebay={"percent": 0, "fixed": 0, "rounding": "none"})
    listing = {"price": 25.50, "poshmark_specifics": {"poshmarkPrice": 25.50}}
    assert price_for(listing, "poshmark") == 26
    assert price_for(listing, "ebay") == 26
    assert sync_unlocked_prices(listing)
    assert listing["poshmark_specifics"]["poshmarkPrice"] == 26
