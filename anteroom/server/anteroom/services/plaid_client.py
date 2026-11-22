"""Narrow async client for the Plaid Transactions product."""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from anteroom.services.shared_http import http_client

HOSTS = {
    "sandbox": "https://sandbox.plaid.com",
    "development": "https://development.plaid.com",
    "production": "https://production.plaid.com",
}


class PlaidError(Exception):
    def __init__(self, code: str = ""):
        self.code = code
        super().__init__(
            "This institution needs to be reconnected in Plaid."
            if code == "ITEM_LOGIN_REQUIRED"
            else "Plaid could not finish this request. Try again shortly."
        )


@dataclass(frozen=True)
class PlaidClient:
    environment: str
    client_id: str = field(repr=False)
    secret: str = field(repr=False)

    @staticmethod
    def configured() -> PlaidClient | None:
        environment = os.getenv("PLAID_ENV", "sandbox").strip()
        client_id = os.getenv("PLAID_CLIENT_ID", "").strip()
        secret = os.getenv("PLAID_SECRET", "").strip()
        if environment not in HOSTS or not client_id or not secret:
            return None
        return PlaidClient(environment, client_id, secret)

    @staticmethod
    def redirect_uri() -> str | None:
        value = os.getenv("PLAID_REDIRECT_URI", "").strip()
        try:
            url = urlsplit(value)
            if (url.scheme == "https" and url.hostname and not url.username
                    and url.path in {"", "/"} and not url.query and not url.fragment):
                return value
        except ValueError:
            pass
        return None

    async def post(self, path: str, **body: Any) -> dict[str, Any]:
        try:
            async with asyncio.timeout(20), http_client() as client:
                response = await client.post(f"{HOSTS[self.environment]}{path}",
                                             json={"client_id": self.client_id, "secret": self.secret, **body})
                payload = response.json()
                if not isinstance(payload, dict):
                    raise PlaidError()
                if not response.is_success:
                    raise PlaidError(payload.get("error_code", ""))
                return payload
        except (ValueError, httpx.HTTPError, TimeoutError):
            raise PlaidError() from None
