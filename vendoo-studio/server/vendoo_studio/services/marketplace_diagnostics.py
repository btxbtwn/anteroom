"""Small typed failure context; never copy marketplace payloads into alerts."""
from __future__ import annotations

import math
import re

_ACTIONS = frozenset({"offer", "share", "list", "publish", "update", "save_draft", "delist", "delete"})
_OPERATIONS = frozenset({"marketplace.execute", "marketplace.delete", "marketplace.promote",
                         "marketplace.promotion_check", "marketplace.promotion_result", "marketplace.sessions",
                         "marketplace.orders", "marketplace.inspect", "marketplace.listings"})
_NUMBERS = frozenset({"elapsed_ms", "timeout_ms", "discount_percent", "price_cents", "offer_price_cents",
                      "floor_cents", "poshmark_price_input", "smart_sell_minimum_input"})
_BOOLEANS = frozenset({"safe_to_retry", "smart_sell_enabled_input"})
_DECIMAL = re.compile(r"\d{1,9}(?:\.\d{1,4})?\Z")


def sanitize_context(value: object) -> dict:
    """Recheck a fixed vocabulary at the email boundary, including value types."""
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in _NUMBERS:
        number = value.get(key)
        if isinstance(number, str) and _DECIMAL.fullmatch(number):
            number = float(number)
        if isinstance(number, (int, float)) and not isinstance(number, bool) and 0 <= number <= 1_000_000_000 and math.isfinite(number):
            result[key] = number
    for key in _BOOLEANS:
        if type(value.get(key)) is bool:
            result[key] = value[key]
    for key, allowed in (("action", _ACTIONS), ("operation", _OPERATIONS)):
        if isinstance(value.get(key), str) and value[key] in allowed:
            result[key] = value[key]
    return result


def failure_context(operation: str, payload: dict | None, result: object, elapsed_s: float, timeout_s: float) -> dict:
    """Inputs are selected values, not proof of what the marketplace accepted.

    In particular the Bridge may round prices or use its form-specific inputs.
    Unknown retry safety is deliberately omitted, never inferred from a timeout.
    No DB/Chrome reads, listing IDs, contents, URLs or shipping labels are logged.
    """
    payload = payload or {}
    values = {"operation": operation, "action": payload.get("action"),
              "elapsed_ms": round(elapsed_s * 1000), "timeout_ms": round(timeout_s * 1000)}
    for key in ("discount_percent", "price_cents", "offer_price_cents", "floor_cents"):
        values[key] = payload.get(key)
    if isinstance(result, dict):
        values["safe_to_retry"] = result.get("safe_to_retry")
    listing = payload.get("listing")
    if payload.get("marketplace") == "poshmark" and isinstance(listing, dict):
        specifics = listing.get("poshmark_specifics")
        specifics = specifics if isinstance(specifics, dict) else {}
        values["poshmark_price_input"] = specifics.get("poshmarkPrice", listing.get("price"))
        smart = specifics.get("smartSell")
        if isinstance(smart, dict):
            values["smart_sell_enabled_input"] = smart.get("enabled")
            values["smart_sell_minimum_input"] = smart.get("minimumPrice")
    return sanitize_context(values)
