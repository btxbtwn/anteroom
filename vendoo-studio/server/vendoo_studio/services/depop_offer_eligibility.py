"""Recognize the local multi-size preview guard for reporting, never send authority."""
from __future__ import annotations


def expected_multisize(marketplace: str, operation: str, action: object, result: object) -> bool:
    """Only a direct, safely refused Depop offer preview can be expected.

    This does not authorize a send or infer that an item stays ineligible after
    its seller edits the sizes. Callers must not lend outer context to children.
    """
    return (
        (marketplace, operation, action) == ("depop", "marketplace.promotion_check", "offer")
        and isinstance(result, dict)
        and result.get("ok") is False
        and result.get("error_code") == "rejected" and result.get("safe_to_retry") is True
        and result.get("error") == "Depop does not send offers on listings with several sizes."
        and not result.get("marketplace_message") and result.get("state") is None
        and not result.get("errors") and result.get("statusCode") is None
        and result.get("marketplace", marketplace) == marketplace
        and result.get("operation", operation) == operation
        and result.get("action", action) == action
    )
