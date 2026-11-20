"""Mask credentials in text before it is logged or shown in Settings.

Bearer tokens, JWTs, and ``key=value`` pairs whose key names a secret (tokens,
API keys, passwords, signed-upload signatures) keep their first four
characters and lose the rest.
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
_JWT = re.compile(r"\beyJ[\w-]{6,}\.[\w-]+\.[\w-]+")
_BEARER = re.compile(r"\b(Bearer\s+)(?!…\(masked\))[\w.~+/=-]+", re.IGNORECASE)
_MASK = "…(masked)"


def _mask(value: str) -> str:
    if value.endswith(_MASK):
        return value
    return f"{value[:4]}{_MASK}"


def redact(text: str) -> str:
    text = _BEARER.sub(lambda m: f"{m.group(1)}{_MASK}", text)
    text = _JWT.sub(lambda m: _mask(m.group(0)), text)
    return _SECRET_PAIR.sub(lambda m: f"{m.group(1)}{_mask(m.group(2))}", text)
