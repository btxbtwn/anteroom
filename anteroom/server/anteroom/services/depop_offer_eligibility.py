"""Recognize expected Depop offer-preview refusals for reporting, never send authority."""
from __future__ import annotations

_MULTISIZE_ERROR = "Depop does not send offers on listings with several sizes."
_NO_RECIPIENTS_ERROR = (
    "Depop has nobody to send an offer to on this listing: "
    "no one has liked it, or every liker already has an offer."
)


def _direct_depop_offer_refusal(marketplace: str, operation: str, action: object, result: object) -> bool:
    return (
        (marketplace, operation, action) == ("depop", "marketplace.promotion_check", "offer")
        and isinstance(result, dict)
        and result.get("ok") is False
        and result.get("error_code") == "rejected" and result.get("safe_to_retry") is True
        and not result.get("marketplace_message") and result.get("state") is None
        and not result.get("errors") and result.get("statusCode") is None
        and result.get("marketplace", marketplace) == marketplace
        and result.get("operation", operation) == operation
        and result.get("action", action) == action
    )


def expected_multisize(marketplace: str, operation: str, action: object, result: object) -> bool:
    """Only a direct, safely refused Depop offer preview can be expected.

    This does not authorize a send or infer that an item stays ineligible after
    its seller edits the sizes. Callers must not lend outer context to children.
    """
    return (
        _direct_depop_offer_refusal(marketplace, operation, action, result)
        and isinstance(result, dict)
        and result.get("error") == _MULTISIZE_ERROR
    )


def expected_no_recipients(marketplace: str, operation: str, action: object, result: object) -> bool:
    """Depop's send-all preview refused with HTTP 400 code 1003 (zero eligible likers).

    The extension translates that into ``like_count: 0`` and this exact message
    so the automation can wait for a new like before checking again. This never
    authorizes a send or hides other failure indicators.
    """
    return (
        _direct_depop_offer_refusal(marketplace, operation, action, result)
        and isinstance(result, dict)
        and result.get("error") == _NO_RECIPIENTS_ERROR
        and type(result.get("like_count")) is int
        and result.get("like_count") == 0
    )


def expected_preview_refusal(marketplace: str, operation: str, action: object, result: object) -> bool:
    """Whether a direct Depop offer preview refusal is an expected eligibility skip."""
    return (
        expected_multisize(marketplace, operation, action, result)
        or expected_no_recipients(marketplace, operation, action, result)
    )
