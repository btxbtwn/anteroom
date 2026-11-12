from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import patch

import httpx

from vendoo_studio.services import marketplace_accounts as accounts
from vendoo_studio.services.relay_client import RelayError


class TokenRefreshTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = {"etsy": {"accountId": "1", "connectedAt": 1, "accessToken": "old", "refreshToken": "r1",
                               "expiresAt": time.time() - 10}}
        self.enterContext(patch.object(accounts, "get_marketplace_accounts", side_effect=lambda: {k: dict(v) for k, v in self.store.items()}))
        self.enterContext(patch.object(accounts, "set_marketplace_account", side_effect=lambda m, a: self.store.__setitem__(m, dict(a))))
        self.enterContext(patch.object(accounts, "update_marketplace_accounts", side_effect=lambda mutate: mutate(self.store)))
        self.refreshes = []

    async def relay(self, method, path, json_body=None, **kwargs):
        if path.endswith("/refresh"):
            self.refreshes.append(json_body["refreshToken"])
            await asyncio.sleep(0.01)
            if json_body["refreshToken"] != self.store["etsy"]["refreshToken"]:
                # Etsy rotates the refresh token: the old one no longer works.
                return httpx.Response(401, json={"error": "sign in again"})
            n = len(self.refreshes)
            return httpx.Response(200, json={"tokens": {"accessToken": f"a{n}", "refreshToken": f"r{n + 1}", "expiresIn": 3600}})
        raise AssertionError(path)

    async def test_concurrent_calls_share_one_refresh(self):
        with patch.object(accounts, "relay_request", side_effect=self.relay):
            tokens = await asyncio.gather(accounts.access_token("etsy"), accounts.access_token("etsy"))
        self.assertEqual(tokens, ["a1", "a1"])
        self.assertEqual(self.refreshes, ["r1"])
        self.assertEqual(self.store["etsy"]["refreshToken"], "r2")

    async def test_a_rejected_token_is_refreshed_once_even_before_it_expires(self):
        self.store["etsy"]["expiresAt"] = time.time() + 3600
        with patch.object(accounts, "relay_request", side_effect=self.relay):
            self.assertEqual(await accounts.access_token("etsy"), "old")
            self.assertEqual(await accounts.access_token("etsy", rejected="old"), "a1")
            # Another caller already replaced the refused token.
            self.assertEqual(await accounts.access_token("etsy", rejected="old"), "a1")
        self.assertEqual(self.refreshes, ["r1"])

    async def test_etsy_401_retries_once_with_a_fresh_token(self):
        self.store["etsy"]["expiresAt"] = time.time() + 3600
        seen = []

        async def relay(method, path, headers=None, **kwargs):
            if path.endswith("/refresh"):
                return await self.relay(method, path, **kwargs)
            seen.append(headers["X-Etsy-Authorization"])
            return httpx.Response(401 if len(seen) == 1 else 200, json={"error": "invalid_token"} if len(seen) == 1 else {"ok": 1},
                                  request=httpx.Request(method, "https://relay/"))

        with patch.object(accounts, "relay_request", side_effect=relay):
            self.assertEqual(await accounts.etsy_api("GET", "shops/1"), {"ok": 1})
        self.assertEqual(seen, ["Bearer old", "Bearer a1"])

    async def test_the_relay_refusing_this_mac_is_not_a_marketplace_sign_in(self):
        self.store["etsy"]["expiresAt"] = time.time() + 3600

        async def relay(method, path, **kwargs):
            return httpx.Response(401, json={"error": "unauthorized"}, request=httpx.Request(method, "https://relay/"))

        with patch.object(accounts, "relay_request", side_effect=relay):
            with self.assertRaises(RelayError) as raised:
                await accounts.etsy_api("GET", "shops/1")
            self.assertNotIsInstance(raised.exception, accounts.SignInRequired)
            self.store["etsy"]["expiresAt"] = time.time() - 10
            with self.assertRaises(RelayError):
                await accounts.access_token("etsy")


def test_refreshes_on_separate_event_loops_send_the_rotating_token_once(monkeypatch):
    """Request and scheduler loops (and other Machines) each refresh; Etsy accepts a refresh token once."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from vendoo_studio.services import keychain

    keychain.set_marketplace_account("etsy", {"accountId": "1", "connectedAt": 1, "accessToken": "old",
                                              "refreshToken": "r1", "expiresAt": time.time() - 10})
    sent, valid, lock = [], {"refreshToken": "r1"}, threading.Lock()
    both_waiting = threading.Barrier(2, timeout=5)

    async def relay(method, path, json_body=None, **kwargs):
        with lock:
            sent.append(json_body["refreshToken"])
        await asyncio.sleep(0.2)
        with lock:
            if json_body["refreshToken"] != valid["refreshToken"]:
                return httpx.Response(401, json={"error": "invalid_grant"})
            valid["refreshToken"] = f"r{len(sent) + 1}"
            return httpx.Response(200, json={"tokens": {"accessToken": f"a{len(sent)}",
                                                        "refreshToken": valid["refreshToken"], "expiresIn": 3600}})

    def on_its_own_loop(_):
        both_waiting.wait()
        return asyncio.run(accounts.access_token("etsy"))

    monkeypatch.setattr(accounts, "relay_request", relay)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tokens = list(pool.map(on_its_own_loop, range(2)))
    assert sent == ["r1"]
    assert tokens == ["a1", "a1"]
    saved = keychain.get_marketplace_accounts()["etsy"]
    assert saved["refreshToken"] == "r2" and "refreshClaim" not in saved
