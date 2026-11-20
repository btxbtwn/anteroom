"""Offer-preview refusals that repeat on every round until something about the listing changes.

Recognizing one never authorizes a send, never changes the listing's recorded
status and never hides the refusal: the first one in each hold is still
reported as an error. It only lets an automation's regular scan wait a day
before reading the same listing again. Observed on the seller's account
(2026-11-15 to 11-17): each of these answered every 15-minute round for days.

- Depop: the product read by slug answered HTTP 404 "Product not found"
  after the account read succeeded. Why the saved link no longer resolves is
  not known, so the listing is not marked as removed.
- Mercari: offerToLikerable, which is sent only the item ID, answered
  "Oops! Your offer can't be less than $1.00." Anteroom sends no price there,
  so this does not show that the listing's price is under any amount.
"""
from __future__ import annotations

from datetime import timedelta

HOLD = timedelta(hours=24)

DEPOP_NOT_FOUND = "Depop could not find a listing at this item's saved Depop link."
MERCARI_REFUSED = "Mercari would not allow an offer to likers on this listing."

# (marketplace, preview step, Chrome's error, the marketplace's own words) -> the seller-facing note.
_RECOGNIZED: dict[tuple[str, object, object, object], str] = {
    ("depop", "depop-product", "Depop refused the request (HTTP 404).", "Product not found"): DEPOP_NOT_FOUND,
    ("mercari", "mercari-eligibility", "Mercari refused the request.", "Oops! Your offer can't be less than $1.00."): MERCARI_REFUSED,
}
NOTES = tuple(_RECOGNIZED.values())


def recognized(marketplace: str, operation: str, action: object, result: object) -> str | None:
    """The note for a direct, safely refused offer preview that matches one of the observed replies exactly."""
    if (operation != "marketplace.promotion_check" or action != "offer"
            or not isinstance(result, dict)
            or result.get("ok") is not False
            or result.get("error_code") != "rejected" or result.get("safe_to_retry") is not True
            or result.get("state") is not None or result.get("errors")
            or result.get("statusCode") is not None
            or result.get("marketplace", marketplace) != marketplace
            or result.get("operation", operation) != operation
            or result.get("action", action) != action):
        return None
    return _RECOGNIZED.get((marketplace, result.get("preview_step"), result.get("error"), result.get("marketplace_message")))


def held_note(note: object, marketplace_message: object) -> bool:
    """Whether a skipped check's journal note is one of these refusals, as the round records it."""
    if not isinstance(note, str):
        return False
    return any(note == text or (isinstance(marketplace_message, str) and note == f"{text.rstrip('.')}: {marketplace_message}")
               for text in NOTES)
