"""Mask credentials in text before it is logged or shown in Settings.

Bearer tokens, JWTs, Postgres DSN passwords, sensitive headers
(Authorization, Cookie, Set-Cookie, X-API-Key), and ``key=value`` pairs
whose key names a secret (tokens, API keys, passwords, signed-upload
signatures) are masked before logging or display.
"""

from __future__ import annotations

import re

_SECRET_KEYS = (
    "id_?token|refresh_?token|access_?token|api_?key|key|token|password|secret"
    "|X-Goog-Signature|X-Goog-Credential|signature|sig"
)
_SECRET_PAIR = re.compile(
    rf"""(["']?\b(?:{_SECRET_KEYS})["']?\s*[:=]\s*["']?)([^"'&\s,}}]{{4,}})""",
    re.IGNORECASE,
)
_HEADER_KEYS = "authorization|set-cookie|cookie|x-api-key"
_QUOTED_HEADER = re.compile(
    rf"""(["']\b(?:{_HEADER_KEYS})\b["']\s*[:=]\s*["'])([^"'\r\n]+)(["'])""",
    re.IGNORECASE,
)
# Raw header lines end at a newline; punctuation (including &, comma and })
# can be part of the credential. Quoted mapping values use their own boundaries.
_UNQUOTED_HEADER = re.compile(
    rf"""(\b(?:{_HEADER_KEYS})\s*[:=]\s*)(?!["'])(?!Bearer\s+…\(masked\))([^\r\n]+)""",
    re.IGNORECASE,
)
_POSTGRES_DSN = re.compile(
    r"(\bpostgres(?:ql)?(?:\+[\w.-]+)?://[^/\s:@]+:)([^/\s?#]+)(@)",
    re.IGNORECASE,
)
_JWT = re.compile(r"\beyJ[\w-]{6,}\.[\w-]+\.[\w-]+")
_BEARER = re.compile(r"\b(Bearer\s+)(?!…\(masked\))[\w.~+/=-]+", re.IGNORECASE)
_MASK = "…(masked)"


def _mask(value: str) -> str:
    if value.endswith(_MASK):
        return value
    return f"{value[:4]}{_MASK}"


def _mask_full(value: str) -> str:
    if value.endswith(_MASK):
        return value
    return _MASK


def redact(text: str) -> str:
    text = _POSTGRES_DSN.sub(lambda m: f"{m.group(1)}{_mask_full(m.group(2))}{m.group(3)}", text)
    text = _BEARER.sub(lambda m: f"{m.group(1)}{_MASK}", text)
    text = _JWT.sub(lambda m: _mask(m.group(0)), text)
    text = _QUOTED_HEADER.sub(lambda m: f"{m.group(1)}{_mask_full(m.group(2))}{m.group(3)}", text)
    text = _UNQUOTED_HEADER.sub(lambda m: f"{m.group(1)}{_mask_full(m.group(2))}", text)
    return _SECRET_PAIR.sub(lambda m: f"{m.group(1)}{_mask(m.group(2))}", text)
