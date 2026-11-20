"""Mask credentials in text before it is logged or shown in Settings.

Bearer tokens, JWTs, Postgres DSN passwords, sensitive headers
(Authorization, Cookie, Set-Cookie, X-API-Key), and ``key=value`` pairs
whose key names a secret (tokens, API keys, passwords, signed-upload
signatures) are masked before logging or display.
"""

from __future__ import annotations

import re

_SECRET_PAIR = re.compile(
    r"""(["']?\b(?:id_?token|refresh_?token|access_?token|api_?key|key|token|password|secret|X-Goog-Signature|X-Goog-Credential|signature|sig)["']?[ \t]*[:=][ \t]*["']?)([^"'&\s,}]{4,})""",
    re.IGNORECASE,
)
_DQ_HEADER = re.compile(
    r"""(["']?\b(?:authorization|set-cookie|cookie|x-api-key)\b["']?[ \t]*[:=][ \t]*")((?:\\.|[^"\\\r\n])*)(")""",
    re.IGNORECASE,
)
_SQ_HEADER = re.compile(
    r"""(["']?\b(?:authorization|set-cookie|cookie|x-api-key)\b["']?[ \t]*[:=][ \t]*')((?:\\.|[^'\\\r\n])*)(')""",
    re.IGNORECASE,
)
# Possessive whitespace prevents backing into a quoted value after its guard.
_UNQUOTED_HEADER = re.compile(
    r"""(\b(?:authorization|set-cookie|cookie|x-api-key)\b[ \t]*[:=][ \t]*+)(?!["'])([^\r\n]+)""",
    re.IGNORECASE,
)
_POSTGRES_DSN = re.compile(
    r"""\bpostgres(?:ql)?(?:\+[\w.-]+)?://[^\s/?#"',)]+""",
    re.IGNORECASE,
)
_JWT = re.compile(r"\beyJ[\w-]{6,}\.[\w-]+\.[\w-]+")
_BEARER = re.compile(r"\b(Bearer\s+)(?!…\(masked\))[\w.~+/=-]+", re.IGNORECASE)
_MASK = "…(masked)"


def _mask(value: str) -> str:
    if value.endswith(_MASK):
        return value
    return f"{value[:4]}{_MASK}"


def _redact_dsn(match: re.Match[str]) -> str:
    dsn = match.group(0)
    sep = dsn.find("://")
    scheme = dsn[: sep + 3]
    authority = dsn[sep + 3 :]
    at = authority.rfind("@")
    if at < 0:
        return dsn
    userinfo = authority[:at]
    host = authority[at + 1 :]
    colon = userinfo.find(":")
    if colon < 0:
        return dsn
    user = userinfo[:colon]
    return f"{scheme}{user}:{_MASK}@{host}"


def redact(text: str) -> str:
    text = _POSTGRES_DSN.sub(_redact_dsn, text)
    text = _BEARER.sub(lambda m: f"{m.group(1)}{_MASK}", text)
    text = _JWT.sub(lambda m: _mask(m.group(0)), text)
    text = _DQ_HEADER.sub(lambda m: f"{m.group(1)}{_MASK}{m.group(3)}", text)
    text = _SQ_HEADER.sub(lambda m: f"{m.group(1)}{_MASK}{m.group(3)}", text)
    text = _UNQUOTED_HEADER.sub(
        lambda m: f"{m.group(1)}"
        + (f"Bearer {_MASK}" if re.fullmatch(r"Bearer\s+…\(masked\)", m.group(2), re.IGNORECASE) else _MASK),
        text,
    )
    return _SECRET_PAIR.sub(lambda m: f"{m.group(1)}{_mask(m.group(2))}", text)
