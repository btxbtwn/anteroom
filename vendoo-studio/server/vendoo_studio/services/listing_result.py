"""The result every direct-listing path reports, whoever did the work.

The Chrome extension, ``etsy_listings`` and ``ebay_listings`` all answer in
this shape (``/tmp`` contract, mirrored in ``vendoo-extension``):

- ``ok``, ``state``, ``draft_url``, ``listing_url``
- ``error``: Anteroom's plain-words explanation
- ``marketplace_message``: the marketplace's own words, verbatim but scrubbed
- ``safe_to_retry``: nothing was written, or the marketplace refused before committing
- ``error_code`` (see ``ERROR_CODES``), with ``fields`` for missing_fields and
  ``retry_after_s`` for rate_limited
"""
from __future__ import annotations

import re

ERROR_CODES = frozenset({
    "signed_out", "challenge", "rate_limited", "missing_fields", "rejected", "schema_drift", "network", "uncertain",
})
MESSAGE_LIMIT = 4000

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Bearer tokens, cookies, signed URLs: long unbroken runs of token characters.
_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{24,}")
_SECRET_PAIR = re.compile(r"(?i)\b(token|cookie|authorization|secret|password)([\"']?\s*[:=]\s*)\S+|\b(bearer)(\s+)\S+")


def scrub(text: object) -> str | None:
    """The marketplace's message as it said it, minus anything private, capped."""
    if text is None:
        return None
    clean = " ".join(str(text).split())
    clean = _SECRET_PAIR.sub(lambda match: f"{match[1] or match[3]}{match[2] or match[4]}[redacted]", clean)
    clean = _EMAIL.sub("[email]", clean)
    clean = _TOKEN.sub("[redacted]", clean)
    if len(clean) > MESSAGE_LIMIT:
        clean = clean[:MESSAGE_LIMIT - 1].rstrip() + "…"
    return clean or None


class ListingProblem(ValueError):
    """A definite refusal found before or instead of a write."""

    def __init__(self, message: str, code: str = "rejected", *, fields: list[dict] | None = None,
                 retry_after_s: int | None = None, marketplace_message: str | None = None):
        super().__init__(message)
        self.code = code
        self.fields = fields
        self.retry_after_s = retry_after_s
        self.marketplace_message = marketplace_message


def missing(message: str, *fields: str) -> ListingProblem:
    """A field the listing needs before the marketplace will take it."""
    return ListingProblem(message, "missing_fields", fields=[{"field": field, "message": message} for field in fields])


def failure(error: str, code: str, *, safe: bool, fields: list[dict] | None = None, retry_after_s: int | None = None,
            marketplace_message: str | None = None, **extra) -> dict:
    result = {"ok": False, "safe_to_retry": safe, "error": error, "error_code": code, **extra}
    if fields:
        result["fields"] = fields
    if retry_after_s is not None:
        result["retry_after_s"] = retry_after_s
    message = scrub(marketplace_message)
    if message:
        result["marketplace_message"] = message
    return result


def retry_after_seconds(value: object) -> int | None:
    """Retry-After in seconds, when the marketplace gave a number."""
    try:
        seconds = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return max(1, min(seconds, 24 * 3600))
