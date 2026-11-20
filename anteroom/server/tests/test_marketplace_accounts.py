from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import patch

import httpx
import pytest

from anteroom.services import marketplace_accounts as accounts
from anteroom.services.relay_client import RelayError


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

    from anteroom.services import keychain

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


class EbayConnectionStatusTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        TokenRefreshTest.setUp(self)
        self.store["ebay"] = dict(self.store["etsy"])

    async def test_refused_refresh_is_visible_in_status_and_stops_repeated_refreshes(self):
        with patch.object(accounts, "relay_request", return_value=httpx.Response(401, json={"error": "invalid_grant"})) as relay:
            for _ in range(2):
                with self.assertRaises(accounts.SignInRequired):
                    await accounts.access_token("ebay")
            state = accounts.status()["accounts"]["ebay"]
        self.assertFalse(state["connected"])
        self.assertIn("Reconnect eBay", state["error"])
        self.assertEqual(relay.call_count, 1)
        self.assertEqual(self.store["ebay"]["refreshToken"], "r1")

    async def test_relay_refusal_and_outage_do_not_sign_the_seller_out(self):
        for status, body in [(401, {"error": "unauthorized"}), (503, {})]:
            with patch.object(accounts, "relay_request", return_value=httpx.Response(status, json=body)):
                with self.assertRaises(RelayError):
                    await accounts.access_token("ebay")
            self.assertTrue(accounts.status()["accounts"]["ebay"]["connected"])

    async def test_refusal_for_old_connection_does_not_mark_new_sign_in(self):
        async def relay(*args, **kwargs):
            self.store["ebay"] = {**self.store["ebay"], "connectedAt": 2, "accessToken": "new"}
            return httpx.Response(401, json={"error": "invalid_grant"})

        with patch.object(accounts, "relay_request", side_effect=relay):
            with self.assertRaises(accounts.SignInRequired):
                await accounts.access_token("ebay")
        self.assertTrue(accounts.status()["accounts"]["ebay"]["connected"])
        self.assertNotIn("authError", self.store["ebay"])


def test_editor_preserves_sign_in_failure_reason(monkeypatch):
    from anteroom.services.ebay_client import EbayClient
    from anteroom.services import native_marketplaces

    async def refused():
        raise accounts.SignInRequired("Reconnect eBay in Settings → Connections. Its sign-in is no longer valid.")

    monkeypatch.setattr(EbayClient, "connect", refused)
    with pytest.raises(native_marketplaces.NativeMarketplaceError, match="Reconnect eBay"):
        asyncio.run(native_marketplaces._require_ebay())


def test_expired_access_token_with_refresh_grant_is_not_assumed_signed_out(monkeypatch):
    saved = {"ebay": {"accessToken": "fixture", "refreshToken": "fixture-refresh", "expiresAt": 1}}
    monkeypatch.setattr(accounts, "get_marketplace_accounts", lambda: saved)
    state = accounts.status()["accounts"]["ebay"]
    assert state["connected"] is True
    assert state["error"] is None


class MarketplaceReconnectRecoveryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        TokenRefreshTest.setUp(self)

    async def test_each_refused_grant_recovers_only_after_a_new_sign_in(self):
        for marketplace in accounts.MARKETPLACES:
            with self.subTest(marketplace=marketplace):
                self.store[marketplace] = {"accountId": "fixture-seller", "connectedAt": 1,
                                           "accessToken": "fixture-old", "refreshToken": "fixture-refresh",
                                           "expiresAt": 1}
                with patch.object(accounts, "relay_request", return_value=httpx.Response(401, json={"error": "invalid_grant"})):
                    with self.assertRaises(accounts.SignInRequired):
                        await accounts.access_token(marketplace)
                state = accounts.status()["accounts"][marketplace]
                self.assertFalse(state["connected"])
                self.assertIn(f"Reconnect {accounts.NAMES[marketplace]}", state["error"])
                self.assertNotIn("fixture-refresh", str(state))
                self.assertNotIn("fixture-old", str(state))
                key = (accounts._seller(), marketplace)
                pending = accounts.PendingSignIn(marketplace, "fixture-state", "https://fixture.invalid")
                self.enterContext(patch.dict(accounts._pending, {key: pending}))
                response = httpx.Response(200, json={"accountId": "fixture-seller", "username": "Fixture seller",
                                                       "tokens": {"accessToken": "fixture-new", "refreshToken": "fixture-new-refresh",
                                                                  "expiresIn": 3600}})
                with patch.object(accounts, "relay_request", return_value=response), patch.object(accounts, "start_listening"):
                    await accounts.finish_sign_in(marketplace, "fixture-state", "fixture-claim")
                state = accounts.status()["accounts"][marketplace]
                self.assertTrue(state["connected"])
                self.assertIsNone(state["error"])
                self.assertNotIn("authError", self.store[marketplace])
                with patch.object(accounts, "relay_request", side_effect=AssertionError("Fresh sign-in must not refresh")):
                    self.assertEqual(await accounts.access_token(marketplace), "fixture-new")
