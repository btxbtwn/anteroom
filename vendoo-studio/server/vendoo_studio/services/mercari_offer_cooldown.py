"""The observed Mercari offer-preview eligibility refusal, not send authority."""
from __future__ import annotations

from datetime import timedelta
import re


def message_wait(message: object) -> timedelta | None:
    """Bound this exact hint; the extra hour is conservative, not proven rounding."""
    if not isinstance(message, str):
        return None
    match = re.fullmatch(r"You can make an offer again in ([1-9][0-9]?) hours\. \(once every 3 days\)", message)
    if match and 1 <= int(match[1]) <= 72:
        return timedelta(hours=int(match[1]) + 1)
    return None


def preview_wait(marketplace: str, operation: str, action: object, result: object) -> timedelta | None:
    """Classify a direct preview reply using its own refusal and safety evidence.

    Callers must not use an outer envelope's context to classify a nested
    reply. Unknown/send outcomes and conflicting failure indicators remain
    errors, even if they happen to include the observed cooldown words.
    """
    if ((marketplace, operation, action) != ("mercari", "marketplace.promotion_check", "offer")
            or not isinstance(result, dict)
            or result.get("ok") is not False
            or result.get("error_code") != "rejected" or result.get("safe_to_retry") is not True
            or result.get("error") != "Mercari refused the request."
            or result.get("state") is not None or result.get("errors")
            or result.get("statusCode") is not None
            or result.get("marketplace", marketplace) != marketplace
            or result.get("operation", operation) != operation
            or result.get("action", action) != action):
        return None
    return message_wait(result.get("marketplace_message"))
