from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import patch

import httpx
import pytest

from anteroom.database import in_worker_session
from anteroom.services import marketplace_accounts as accounts
from anteroom.services.relay_client import RelayError


def clear_sign_ins():
    """Sign-ins in progress are rows now, so they outlive the test that started them."""
    from anteroom.database import SessionLocal
    from anteroom.models.marketplace_sign_in import MarketplaceSignIn

    with SessionLocal() as db:
        db.query(MarketplaceSignIn).delete()
        db.commit()


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
        clear_sign_ins()
        self.addCleanup(clear_sign_ins)

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
                await in_worker_session(accounts._save_sign_in, marketplace, "fixture-state", "https://fixture.invalid")
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


# Sign-ins in progress survive the process that started them: the hosted API
# deploys blue-green on every merge, and may run on more than one Machine.

class _Relay:
    """The relay: a new state per start; the tokens once, only with the claim."""

    def __init__(self):
        self.starts = 0
        self.claims: list[str] = []
        self.unreachable = 0

    async def __call__(self, method, path, params=None, **kwargs):
        params = params or {}
        if path.endswith("/start"):
            self.starts += 1
            return httpx.Response(200, json={"state": f"state-{self.starts}", "authorizeUrl": f"https://auth.example/{self.starts}"})
        if path == "/studio/oauth/result" and params.get("claim"):
            if self.unreachable:
                self.unreachable -= 1
                raise httpx.ConnectError("offline")
            await asyncio.sleep(0.01)
            self.claims.append(params["state"])
            if self.claims.count(params["state"]) > 1:
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(200, json={"accountId": "fixture-account", "username": "fixture_closet",
                                             "tokens": {"accessToken": "fixture-access", "refreshToken": "fixture-refresh",
                                                        "expiresIn": 3600}})
        if path == "/studio/oauth/result":
            return httpx.Response(202, json={"status": "pending"})
        raise AssertionError(path)


@pytest.fixture
def sign_in(monkeypatch):
    clear = clear_sign_ins
    clear()
    relay, saved = _Relay(), {}
    monkeypatch.setattr(accounts, "relay_request", relay)
    monkeypatch.setattr(accounts, "POLL_INTERVAL_S", 60)
    monkeypatch.setattr(accounts.webbrowser, "open", lambda url: True)
    monkeypatch.setattr(accounts, "start_listening", lambda: None)
    monkeypatch.setattr(accounts, "get_marketplace_accounts", lambda: dict(saved))
    monkeypatch.setattr(accounts, "set_marketplace_account", lambda name, account: saved.__setitem__(name, account))
    yield relay, saved
    clear()


def _new_process(monkeypatch):
    """A process that never saw the sign-in start: after a deploy, or another Machine."""
    for name, value in list(vars(accounts).items()):
        if name.startswith("_") and not name.startswith("__") and isinstance(value, dict):
            monkeypatch.setattr(accounts, name, {})


def test_a_sign_in_finishes_on_another_process_than_the_one_that_started_it(sign_in, monkeypatch):
    relay, saved = sign_in
    started = asyncio.run(accounts.start_sign_in("etsy"))
    assert started == {"authorizeUrl": "https://auth.example/1"}

    _new_process(monkeypatch)
    pending = accounts.status()["accounts"]["etsy"]
    assert pending["pending"] and pending["authorizeUrl"] == "https://auth.example/1"
    done = asyncio.run(accounts.finish_sign_in("etsy", "state-1", "claim-from-browser"))
    assert done == {"marketplace": "etsy", "label": "fixture_closet"}
    assert saved["etsy"]["accessToken"] == "fixture-access"
    assert relay.claims == ["state-1"]
    finished = accounts.status()["accounts"]["etsy"]
    assert finished["connected"] and not finished["pending"] and finished["error"] is None


def test_a_finished_sign_in_cannot_be_finished_again(sign_in):
    relay, _ = sign_in
    asyncio.run(accounts.start_sign_in("ebay"))
    asyncio.run(accounts.finish_sign_in("ebay", "state-1", "claim"))
    with pytest.raises(accounts.SignInNotFound):
        asyncio.run(accounts.finish_sign_in("ebay", "state-1", "claim"))
    assert relay.claims == ["state-1"]


def test_two_finishes_at_once_collect_the_tokens_once(sign_in):
    relay, _ = sign_in

    async def run():
        await accounts.start_sign_in("etsy")
        return await asyncio.gather(*(accounts.finish_sign_in("etsy", "state-1", "claim") for _ in range(2)),
                                    return_exceptions=True)

    outcomes = asyncio.run(run())
    assert sorted(type(outcome).__name__ for outcome in outcomes) == ["SignInNotFound", "dict"]
    assert relay.claims == ["state-1"]
    assert accounts.status()["accounts"]["etsy"]["error"] is None


def test_an_expired_sign_in_is_refused(sign_in, monkeypatch):
    from datetime import timedelta

    relay, saved = sign_in
    asyncio.run(accounts.start_sign_in("etsy"))
    later = accounts._now() + timedelta(seconds=accounts.SIGN_IN_TIMEOUT_S + 1)
    monkeypatch.setattr(accounts, "_now", lambda: later)
    _new_process(monkeypatch)
    status = accounts.status()["accounts"]["etsy"]
    assert not status["pending"] and status["authorizeUrl"] is None
    assert "expired" in status["error"]
    with pytest.raises(accounts.SignInNotFound):
        asyncio.run(accounts.finish_sign_in("etsy", "state-1", "claim"))
    assert relay.claims == [] and saved == {}


def test_only_the_latest_state_finishes(sign_in):
    relay, _ = sign_in
    asyncio.run(accounts.start_sign_in("etsy"))
    asyncio.run(accounts.start_sign_in("etsy"))
    for state in ("state-1", "made-up"):
        with pytest.raises(accounts.SignInNotFound):
            asyncio.run(accounts.finish_sign_in("etsy", state, "claim"))
    # A finish for the other marketplace does not take this one.
    with pytest.raises(accounts.SignInNotFound):
        asyncio.run(accounts.finish_sign_in("ebay", "state-2", "claim"))
    assert asyncio.run(accounts.finish_sign_in("etsy", "state-2", "claim"))["label"] == "fixture_closet"
    assert relay.claims == ["state-2"]


def test_a_finish_that_cannot_reach_the_relay_can_be_retried(sign_in):
    relay, _ = sign_in
    relay.unreachable = 1
    asyncio.run(accounts.start_sign_in("etsy"))
    with pytest.raises(httpx.ConnectError):
        asyncio.run(accounts.finish_sign_in("etsy", "state-1", "claim"))
    assert asyncio.run(accounts.finish_sign_in("etsy", "state-1", "claim"))["label"] == "fixture_closet"


def test_a_finish_the_relay_refuses_before_answering_can_be_retried(sign_in, monkeypatch):
    relay, _ = sign_in

    async def busy(method, path, params=None, **kwargs):
        if path == "/studio/oauth/result" and params and "claim" in params:
            raise RelayError("Anteroom's relay is busy. Try again in a minute.", 429)
        return await relay(method, path, params=params, **kwargs)

    asyncio.run(accounts.start_sign_in("etsy"))
    monkeypatch.setattr(accounts, "relay_request", busy)
    with pytest.raises(RelayError, match="busy"):
        asyncio.run(accounts.finish_sign_in("etsy", "state-1", "claim"))
    assert accounts.status()["accounts"]["etsy"]["pending"]
    monkeypatch.setattr(accounts, "relay_request", relay)
    assert asyncio.run(accounts.finish_sign_in("etsy", "state-1", "claim"))["label"] == "fixture_closet"


def test_a_finish_cancelled_while_waiting_on_the_relay_can_be_retried(sign_in, monkeypatch):
    relay, _ = sign_in

    async def slow(method, path, params=None, **kwargs):
        if path == "/studio/oauth/result" and params and "claim" in params:
            await asyncio.sleep(60)
        return await relay(method, path, params=params, **kwargs)

    async def cancelled_finish():
        task = asyncio.create_task(accounts.finish_sign_in("etsy", "state-1", "claim"))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(accounts.start_sign_in("etsy"))
    monkeypatch.setattr(accounts, "relay_request", slow)
    asyncio.run(cancelled_finish())
    assert accounts.status()["accounts"]["etsy"]["pending"]
    monkeypatch.setattr(accounts, "relay_request", relay)
    assert asyncio.run(accounts.finish_sign_in("etsy", "state-1", "claim"))["label"] == "fixture_closet"


def test_a_refused_claim_ends_the_sign_in_with_its_reason(sign_in, monkeypatch):
    async def refused(method, path, params=None, **kwargs):
        if path.endswith("/start"):
            return httpx.Response(200, json={"state": "state-1", "authorizeUrl": "https://auth.example/1"})
        return httpx.Response(200, json={"error": "The seller declined."})

    monkeypatch.setattr(accounts, "relay_request", refused)
    asyncio.run(accounts.start_sign_in("ebay"))
    with pytest.raises(RelayError, match="declined"):
        asyncio.run(accounts.finish_sign_in("ebay", "state-1", "claim"))
    status = accounts.status()["accounts"]["ebay"]
    assert not status["pending"] and status["error"] == "The seller declined."
    with pytest.raises(accounts.SignInNotFound):
        asyncio.run(accounts.finish_sign_in("ebay", "state-1", "claim"))


def test_a_cancelled_sign_in_is_forgotten(sign_in):
    asyncio.run(accounts.start_sign_in("etsy"))
    accounts.cancel_sign_in("etsy")
    assert not accounts.status()["accounts"]["etsy"]["pending"]
    with pytest.raises(accounts.SignInNotFound):
        asyncio.run(accounts.finish_sign_in("etsy", "state-1", "claim"))


def test_the_watcher_reports_a_sign_in_the_relay_ended(sign_in, monkeypatch):
    async def declined(method, path, params=None, **kwargs):
        if path.endswith("/start"):
            return httpx.Response(200, json={"state": "state-1", "authorizeUrl": "https://auth.example/1"})
        return httpx.Response(200, json={"error": "Etsy sign-in was declined."})

    monkeypatch.setattr(accounts, "relay_request", declined)
    monkeypatch.setattr(accounts, "POLL_INTERVAL_S", 0)

    async def run():
        await accounts.start_sign_in("etsy")
        await accounts._watchers[(accounts._seller(), "etsy")]

    asyncio.run(run())
    status = accounts.status()["accounts"]["etsy"]
    assert not status["pending"] and status["error"] == "Etsy sign-in was declined."
