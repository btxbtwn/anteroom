"""The seller's pricing rules: each marketplace's price, worked out from one anchor price.

With rules on, a marketplace price the seller has not typed follows the anchor
(the item price, or one marketplace's price) through that marketplace's rule:
a percent adjustment, a fixed dollar offset and an ending. The default rules
offset each marketplace's typical selling fees, so the seller keeps about the
same amount wherever the item sells. A price the seller typed on a marketplace
stays theirs. Nothing here sends a price anywhere: rules change local prices,
and an item already live shows unsent edits until the seller chooses Update.
"""
from __future__ import annotations

import copy
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Any, Literal

PRICING_RULES_KEY = "pricing_rules"
SNAPSHOT_RULES = "_approved_pricing_rules"
_reviewed_rules: ContextVar[dict | None | Literal[True]] = ContextVar("reviewed_pricing_rules", default=True)
MARKETPLACES = ("ebay", "poshmark", "mercari", "depop", "etsy")
ANCHORS = ("general", *MARKETPLACES)
ROUNDINGS = ("none", "99", "whole")
# Poshmark only takes whole-dollar prices.
WHOLE_DOLLAR_ONLY = frozenset({"poshmark"})
MAX_PERCENT = 500.0
MIN_PERCENT = -90.0
MAX_FIXED = 1000.0

# Typical seller fees in the US: (percent of the sale, fixed amount per order).
# They are starting points for the default rules, not a promise of what any
# one sale costs; the seller can change every rule.
TYPICAL_FEES: dict[str, tuple[float, float]] = {
    "ebay": (13.6, 0.40),
    "poshmark": (20.0, 0.0),
    "mercari": (10.0, 0.50),
    "depop": (3.3, 0.45),
    "etsy": (9.5, 0.45),
}
FEE_NOTES = {
    "ebay": "About 13.6% + $0.40 per order",
    "poshmark": "20% (whole dollars only)",
    "mercari": "About 10% + $0.50 per order",
    "depop": "About 3.3% + $0.45 processing",
    "etsy": "About 9.5% + $0.45 with listing fee",
}

_CENT = Decimal("0.01")


def _fees(market: str) -> tuple[Decimal, Decimal]:
    if market not in TYPICAL_FEES:
        return Decimal(0), Decimal(0)
    percent, fixed = TYPICAL_FEES[market]
    return Decimal(str(percent)) / 100, Decimal(str(fixed))


def default_rule(market: str, anchor: str = "general") -> dict:
    """The rule that keeps the seller's take near the anchor's, after typical fees.

    Selling at ``p`` on a marketplace keeps ``p * (1 - fee) - fixed``. Keeping the
    same as the anchor gives ``p = anchor * (1 - fee_a) / (1 - fee_m) + (fixed_m - fixed_a) / (1 - fee_m)``.
    """
    fee_m, fixed_m = _fees(market)
    fee_a, fixed_a = _fees(anchor)
    keep = 1 - fee_m
    percent = ((1 - fee_a) / keep - 1) * 100
    fixed = (fixed_m - fixed_a) / keep
    return {
        "percent": float(percent.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)),
        "fixed": float(fixed.quantize(_CENT, rounding=ROUND_HALF_UP)),
        "rounding": "whole" if market in WHOLE_DOLLAR_ONLY else "none",
    }


def default_rules(anchor: str = "general") -> dict[str, dict]:
    return {market: default_rule(market, anchor) for market in MARKETPLACES}


def _number(value: Any, low: float, high: float, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"Enter a number for the {name}.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Enter a number for the {name}.") from None
    if number != number or number < low or number > high:
        raise ValueError(f"The {name} must be between {low:g} and {high:g}.")
    return round(number, 2)


def clean_rule(raw: Any, market: str) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Each pricing rule needs a percent, a dollar offset and an ending.")
    rounding = str(raw.get("rounding") or "none")
    if rounding not in ROUNDINGS:
        raise ValueError("Choose cents, .99 or whole dollars for the price ending.")
    if market in WHOLE_DOLLAR_ONLY:
        rounding = "whole"
    return {
        "percent": _number(raw.get("percent", 0), MIN_PERCENT, MAX_PERCENT, "percent adjustment"),
        "fixed": _number(raw.get("fixed", 0), -MAX_FIXED, MAX_FIXED, "dollar offset"),
        "rounding": rounding,
    }


def normalize(raw: Any) -> dict:
    """The stored rules, every marketplace filled in. Unknown or broken values fall back to defaults."""
    stored = raw if isinstance(raw, dict) else {}
    anchor = str(stored.get("anchor") or "general")
    if anchor not in ANCHORS:
        anchor = "general"
    raw_rules = stored.get("rules")
    rules: dict = raw_rules if isinstance(raw_rules, dict) else {}
    filled = {}
    for market in MARKETPLACES:
        try:
            filled[market] = clean_rule(rules[market], market) if market in rules else default_rule(market, anchor)
        except ValueError:
            filled[market] = default_rule(market, anchor)
    return {"enabled": stored.get("enabled") is True, "anchor": anchor, "rules": filled}


def get_pricing_rules() -> dict:
    from anteroom.services.user_settings import read_settings

    return normalize(read_settings().get(PRICING_RULES_KEY))


def set_pricing_rules(*, enabled: Any = None, anchor: Any = None, rules: Any = None) -> dict:
    """Save the seller's rules. A field left out stays as it is."""
    from anteroom.services.user_settings import update_settings

    if anchor is not None and anchor not in ANCHORS:
        raise ValueError("Choose the item price or a marketplace as the main price.")
    if enabled is not None and not isinstance(enabled, bool):
        raise ValueError("Choose whether pricing rules are on.")
    cleaned = None
    if rules is not None:
        if not isinstance(rules, dict) or any(market not in MARKETPLACES for market in rules):
            raise ValueError("Pricing rules name eBay, Poshmark, Mercari, Depop or Etsy.")
        cleaned = {market: clean_rule(value, market) for market, value in rules.items()}

    def mutate(payload: dict) -> None:
        current = normalize(payload.get(PRICING_RULES_KEY))
        if enabled is not None:
            current["enabled"] = enabled
        if anchor is not None:
            current["anchor"] = anchor
        if cleaned is not None:
            current["rules"] = {**current["rules"], **cleaned}
        payload[PRICING_RULES_KEY] = current

    return normalize(update_settings(mutate).get(PRICING_RULES_KEY))


def round_price(amount: Decimal, rounding: str, market: str) -> Decimal:
    """Apply an ending. Endings round up, so a rule never prices below the fee offset."""
    amount = amount.quantize(_CENT, rounding=ROUND_HALF_UP)
    if market in WHOLE_DOLLAR_ONLY or rounding == "whole":
        return amount.to_integral_value(rounding=ROUND_CEILING)
    if rounding == "99":
        # The nearest price ending in .99 at or above the amount: 25.00 → 25.99, 25.99 stays.
        return (amount + _CENT).to_integral_value(rounding=ROUND_CEILING) - _CENT
    return amount


def apply_rule(anchor_price: Decimal, rule: dict, market: str) -> Decimal | None:
    raw = anchor_price * (1 + Decimal(str(rule.get("percent", 0))) / 100) + Decimal(str(rule.get("fixed", 0)))
    if raw <= 0:
        return None
    priced = round_price(raw, str(rule.get("rounding") or "none"), market)
    return priced if priced > 0 else None


def active_rules(config: dict | None = None) -> dict | None:
    """The rules when the seller turned them on, else None."""
    if config is None:
        reviewed = _reviewed_rules.get()
        if reviewed is not True:
            return reviewed
    rules = config if config is not None else get_pricing_rules()
    return rules if rules.get("enabled") else None


@contextmanager
def freeze_active_rules(rules: dict | None | Literal[True] = True) -> Iterator[dict | None]:
    """Use one seller settings read for review validation and subsequent dispatch."""
    rules = active_rules() if rules is True else rules
    token = _reviewed_rules.set(rules)
    try:
        yield rules
    finally:
        _reviewed_rules.reset(token)


def anchor_amount(listing: dict, rules: dict) -> Decimal | None:
    """The price the rules start from: the item price, or the anchor marketplace's own price."""
    from anteroom.services.seller_overrides import _market_locked, get_path, locked_paths, money

    anchor = rules.get("anchor") or "general"
    if anchor != "general":
        # The anchor marketplace follows the item price until the seller types its own.
        held = _market_locked(locked_paths(listing), anchor)
        if held:
            amount = money(get_path(listing, held))
            if amount is not None:
                return amount
    fallback = money(listing.get("price"))
    return round_price(fallback, "none", anchor) if fallback is not None and anchor in WHOLE_DOLLAR_ONLY else fallback


def rule_price(listing: dict, market: str, rules: dict) -> Decimal | None:
    """What a marketplace that follows the anchor is priced at under the rules."""
    anchor = anchor_amount(listing, rules)
    if anchor is None:
        return None
    if market == (rules.get("anchor") or "general"):
        return anchor
    rule = (rules.get("rules") or {}).get(market)
    if not isinstance(rule, dict):
        return anchor
    return apply_rule(anchor, rule, market)


def _number_out(amount: Decimal | None) -> int | float | None:
    if amount is None:
        return None
    return int(amount) if amount == amount.to_integral_value() else float(amount)


def price_drift(listing: dict, rules: dict | None, live_prices: dict[str, Any] | None = None,
                markets=None) -> list[dict]:
    """Marketplaces whose price is not what the rules give.

    A price the seller typed on a marketplace (a locked override) that differs,
    and a live listing whose known price differs, both drift. Prices that
    follow the anchor are recomputed on every save, so they never drift.
    """
    from anteroom.services.seller_overrides import _market_locked, get_path, locked_paths, money

    if not rules or not isinstance(listing, dict):
        return []
    anchor = rules.get("anchor") or "general"
    locks = locked_paths(listing)
    live_prices = live_prices or {}
    found = []
    for market in markets or MARKETPLACES:
        if market == anchor:
            continue
        expected = rule_price(listing, market, rules)
        if expected is None:
            continue
        held = _market_locked(locks, market)
        local = money(get_path(listing, held)) if held else None
        live = money(live_prices.get(market))
        if local is not None and local != expected:
            found.append({"marketplace": market, "expected": _number_out(expected), "price": _number_out(local),
                          "source": "override"})
        elif live is not None and live != expected:
            found.append({"marketplace": market, "expected": _number_out(expected), "price": _number_out(live),
                          "source": "live"})
    return found


def apply_rules_to_listing(listing: dict, rules: dict | None = None, markets=None) -> tuple[dict, list[str]]:
    """A copy of the listing with every marketplace price back on the rules.

    Marketplace price overrides are released so the prices follow the anchor
    again. The anchor marketplace keeps its own price. Local only: an item
    already live reads as having unsent edits until the seller chooses Update.
    """
    from anteroom.services.seller_overrides import (
        CANONICAL_PRICE,
        _price_paths,
        _write_locks,
        is_price_path,
        locked_paths,
        marketplace_of,
        set_path,
    )

    rules = rules if rules is not None else get_pricing_rules()
    updated = copy.deepcopy(listing) if isinstance(listing, dict) else {}
    anchor = rules.get("anchor") or "general"
    wanted = [market for market in (markets or MARKETPLACES) if market != anchor]
    changed: list[str] = []
    locks = locked_paths(updated)
    for market in wanted:
        expected = _number_out(rule_price(updated, market, rules))
        if expected is None:
            continue
        paths = [path for path in dict.fromkeys([CANONICAL_PRICE[market], *_price_paths(updated)])
                 if marketplace_of(path) == market]
        before = {path: _get(updated, path) for path in paths}
        released = {path for path in locks if marketplace_of(path) == market and is_price_path(path)}
        locks -= released
        has_block = isinstance(updated.get(f"{market}_specifics"), dict)
        for path in paths:
            if before[path] is not None or (path == CANONICAL_PRICE[market] and has_block):
                set_path(updated, path, expected)
        if released or any(_get(updated, path) != before[path] for path in paths):
            changed.append(market)
    _write_locks(updated, locks)
    return updated, changed


def _get(listing: dict, path: str) -> Any:
    from anteroom.services.seller_overrides import get_path

    return get_path(listing, path)


def settings_payload(config: dict | None = None) -> dict:
    config = config or get_pricing_rules()
    return {
        **config,
        "defaults": default_rules(config["anchor"]),
        "fees": {market: FEE_NOTES[market] for market in MARKETPLACES},
        "anchors": list(ANCHORS),
        "roundings": list(ROUNDINGS),
    }
