"""Verify Supabase seller identity using the project's public signing keys."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

import jwt
from jwt.exceptions import PyJWKClientConnectionError


class InvalidSession(Exception):
    pass


class SessionVerificationUnavailable(Exception):
    pass


@dataclass(frozen=True)
class SellerIdentity:
    user_id: str


class SessionVerifier:
    def __init__(self, supabase_url: str):
        url = urlsplit(supabase_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise ValueError("SUPABASE_URL must be the project's HTTPS origin")
        self.issuer = supabase_url.rstrip("/") + "/auth/v1"
        # Cache the key set, not individual keys forever: retired keys must
        # eventually stop working. PyJWT refreshes the set for a new key ID.
        self.keys = jwt.PyJWKClient(
            self.issuer + "/.well-known/jwks.json", lifespan=600, timeout=5,
        )

    def verify(self, token: str) -> SellerIdentity:
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in {"ES256", "RS256"} or not header.get("kid"):
                raise InvalidSession
            key = self.keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["ES256", "RS256"],
                issuer=self.issuer,
                audience="authenticated",
                options={"require": ["exp", "iat", "iss", "aud", "sub", "role", "session_id"]},
            )
            if claims["role"] != "authenticated" or claims.get("is_anonymous") is not False:
                raise InvalidSession
            user_id = str(UUID(claims["sub"]))
            UUID(claims["session_id"])
        except PyJWKClientConnectionError:
            raise SessionVerificationUnavailable from None
        except (jwt.PyJWTError, ValueError, TypeError, AttributeError):
            raise InvalidSession from None
        return SellerIdentity(user_id=user_id)
