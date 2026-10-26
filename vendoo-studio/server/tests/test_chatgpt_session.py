from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from vendoo_studio.services import chatgpt_oauth as oauth
from vendoo_studio.services import keychain

ISSUER = "https://auth.openai.com"
DISCOVERY = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/api/accounts/authorize",
    "token_endpoint": f"{ISSUER}/api/accounts/oauth/token",
    "jwks_uri": f"{ISSUER}/.well-known/jwks.json",
    "revocation_endpoint": f"{ISSUER}/api/accounts/oauth/revoke",
}
SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWK = {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(SIGNING_KEY.public_key())), "kid": "k1", "alg": "RS256"}
SCOPES = f"openid profile email offline_access resource.invoke {oauth.REQUIRED_SCOPE}"


def id_token(client_id: str, nonce: str, sub: str = "user-1", email: str = "seller@example.com", **extra) -> str:
    claims = {"iss": ISSUER, "aud": client_id, "sub": sub, "email": email, "nonce": nonce,
              "exp": int(time.time()) + 600, **extra}
    return jwt.encode(claims, SIGNING_KEY, algorithm="RS256", headers={"kid": "k1"})


def saved_record(**changes) -> dict:
    return {
        "client_id": "oaiapp_1",
        "access_token": "old-access",
        "refresh_token": "old-refresh",
        "id_token": "",
        "expires_at": 0,
        "earliest_refresh_at": None,
        "scopes": SCOPES.split(),
        "subject": "user-1",
        "email": "seller@example.com",
        **changes,
    }


class ChatGPTTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.requests: list[httpx.Request] = []
        self.token_handler = None
        real = httpx.AsyncClient

        async def transport(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            url = str(request.url)
            if url == oauth.DISCOVERY_URL:
                return httpx.Response(200, json=DISCOVERY)
            if url == DISCOVERY["jwks_uri"]:
                return httpx.Response(200, json={"keys": [JWK]})
            if url == DISCOVERY["token_endpoint"]:
                return await self.token_handler(request)
            if url == DISCOVERY["revocation_endpoint"]:
                return httpx.Response(200)
            return httpx.Response(404)

        def client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(transport)
            return real(*args, **kwargs)

        for item in (
            patch.object(oauth.httpx, "AsyncClient", client),
            patch.object(oauth, "_refresh_lock", asyncio.Lock()),
            patch.object(oauth, "_discovery", None),
            patch.object(oauth, "_pending", None),
            patch.object(oauth.webbrowser, "open", lambda url: None),
        ):
            item.start()
            self.addCleanup(item.stop)

    def token_requests(self) -> list[dict]:
        return [
            {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
            for request in self.requests
            if str(request.url) == DISCOVERY["token_endpoint"]
        ]


class RefreshTest(ChatGPTTestCase):
    async def test_unexpired_connection_does_not_need_the_network(self):
        keychain.set_chatgpt_tokens(saved_record(expires_at=time.time() + 3600))
        self.assertEqual((await oauth.refresh_chatgpt_tokens())["access_token"], "old-access")
        self.assertEqual(self.requests, [])

    async def test_refresh_reuses_the_registered_client_and_resource(self):
        async def respond(request):
            return httpx.Response(200, json={"access_token": "new", "refresh_token": "new-refresh",
                                             "token_type": "Bearer", "expires_in": 3600, "scope": SCOPES})

        self.token_handler = respond
        keychain.set_chatgpt_tokens(saved_record())
        record = await oauth.refresh_chatgpt_tokens()
        self.assertEqual((record["access_token"], record["refresh_token"]), ("new", "new-refresh"))
        self.assertEqual(keychain.get_chatgpt_tokens()["access_token"], "new")
        self.assertEqual(self.token_requests(), [{
            "grant_type": "refresh_token", "client_id": "oaiapp_1",
            "refresh_token": "old-refresh", "resource": oauth.RESOURCE,
        }])

    async def test_concurrent_refreshes_rotate_once(self):
        async def respond(request):
            await asyncio.sleep(0)
            return httpx.Response(200, json={"access_token": "new", "refresh_token": "new-refresh",
                                             "token_type": "bearer", "expires_in": 3600, "scope": SCOPES})

        self.token_handler = respond
        keychain.set_chatgpt_tokens(saved_record())
        results = await asyncio.gather(*(oauth.refresh_chatgpt_tokens() for _ in range(4)))
        self.assertEqual([item["access_token"] for item in results], ["new"] * 4)
        self.assertEqual(len(self.token_requests()), 1)

    async def test_temporary_failures_keep_the_saved_connection(self):
        for status, body in ((429, {"error": "rate_limited"}), (500, {"error": "invalid_grant"}),
                             (502, None), (400, {"error": "invalid_client"})):
            with self.subTest(status=status):
                keychain.set_chatgpt_tokens(saved_record())

                async def respond(request, status=status, body=body):
                    return httpx.Response(status, json=body)

                self.token_handler = respond
                with self.assertRaisesRegex(RuntimeError, "saved sign-in has been kept"):
                    await oauth.refresh_chatgpt_tokens()
                self.assertTrue(oauth.chatgpt_signed_in())

    async def test_rejected_refresh_token_signs_out(self):
        for error in ("invalid_grant", "refresh_token_reused", "refresh_token_expired"):
            with self.subTest(error=error):
                keychain.set_chatgpt_tokens(saved_record())

                async def respond(request, error=error):
                    return httpx.Response(400, json={"error": error})

                self.token_handler = respond
                with self.assertRaisesRegex(RuntimeError, "Sign in again"):
                    await oauth.refresh_chatgpt_tokens()
                self.assertFalse(oauth.chatgpt_signed_in())

    async def test_sign_out_during_refresh_wins(self):
        async def respond(request):
            keychain.delete_chatgpt_tokens()
            return httpx.Response(200, json={"access_token": "new", "refresh_token": "r",
                                             "token_type": "bearer", "expires_in": 3600, "scope": SCOPES})

        self.token_handler = respond
        keychain.set_chatgpt_tokens(saved_record())
        self.assertIsNone(await oauth.refresh_chatgpt_tokens())
        self.assertIsNone(keychain.get_chatgpt_tokens())

    async def test_malformed_renewal_keeps_the_saved_connection(self):
        async def respond(request):
            return httpx.Response(200, json={"expires_in": 3600})

        self.token_handler = respond
        keychain.set_chatgpt_tokens(saved_record())
        with self.assertRaisesRegex(RuntimeError, "invalid token response"):
            await oauth.refresh_chatgpt_tokens()
        self.assertEqual(keychain.get_chatgpt_tokens()["access_token"], "old-access")

    async def test_too_early_to_renew_uses_the_token_while_it_lasts(self):
        keychain.set_chatgpt_tokens(saved_record(expires_at=time.time() + 30, earliest_refresh_at=time.time() + 300))
        self.assertEqual((await oauth.refresh_chatgpt_tokens())["access_token"], "old-access")
        self.assertEqual(self.requests, [])

    async def test_a_codex_sign_in_from_before_is_dropped(self):
        keychain.set_chatgpt_tokens({"access_token": "codex", "refresh_token": "r", "expires_at": 0})
        self.assertFalse(oauth.chatgpt_signed_in())
        self.assertIsNone(keychain.get_chatgpt_tokens())
        self.assertIsNone(await oauth.refresh_chatgpt_tokens())


class SignInTest(ChatGPTTestCase):
    def setUp(self):
        super().setUp()
        self.issued_client = "oaiapp_new"
        self.scope = SCOPES
        self.nonce_override = None

        async def exchange(request):
            body = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
            nonce = self.nonce_override or self.authorize["nonce"]
            return httpx.Response(200, json={
                "access_token": "access-1", "refresh_token": "refresh-1", "token_type": "Bearer",
                "expires_in": 3600, "scope": self.scope,
                "id_token": id_token(body["client_id"], nonce),
            })

        self.token_handler = exchange

    async def start(self) -> dict:
        result = await oauth.start_login()
        self.authorize = {key: values[0] for key, values in parse_qs(urlsplit(result["authorize_url"]).query).items()}
        return self.authorize

    async def browser_returns(self, **params) -> str:
        url = f"{self.authorize['redirect_uri']}?{urlencode(params)}"

        def fetch() -> str:
            try:
                with urllib.request.urlopen(url, timeout=10) as resp:
                    return resp.read().decode()
            except urllib.error.HTTPError as exc:
                return f"HTTP {exc.code}"
            except OSError:
                return "refused"

        page = await asyncio.to_thread(fetch)
        if oauth._pending and oauth._pending.task:
            await oauth._pending.task
        return page

    async def test_first_sign_in_registers_and_saves_the_connection(self):
        params = await self.start()
        self.assertEqual(params["client_id"], oauth.FIRST_CLIENT_ID)
        self.assertEqual(params["agent_name_hint"], "Anteroom")
        self.assertEqual(params["scope"], oauth.SCOPE)
        self.assertEqual(params["resource"], "https://api.openai.com/v1")
        self.assertEqual(params["code_challenge_method"], "S256")
        self.assertRegex(params["redirect_uri"], r"^http://127\.0\.0\.1:\d+/auth/callback$")
        self.assertTrue(params["ext_agent_host_id"].startswith("urn:uuid:"))

        page = await self.browser_returns(code="code-1", state=params["state"], client_id=self.issued_client)
        self.assertIn("ChatGPT is connected", page)
        self.assertIsNone(oauth.login_error())
        record = keychain.get_chatgpt_tokens()
        self.assertEqual((record["client_id"], record["access_token"], record["email"]),
                         ("oaiapp_new", "access-1", "seller@example.com"))
        self.assertTrue(oauth.chatgpt_signed_in())
        registration = keychain.get_chatgpt_registration()
        self.assertEqual((registration["client_id"], registration["subject"]), ("oaiapp_new", "user-1"))

        exchange = self.token_requests()[0]
        self.assertEqual(exchange["grant_type"], "authorization_code")
        self.assertEqual(exchange["redirect_uri"], params["redirect_uri"])
        self.assertEqual(exchange["client_id"], "oaiapp_new")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(exchange["code_verifier"].encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(challenge, params["code_challenge"])

    async def test_later_sign_ins_reuse_the_registration(self):
        keychain.set_chatgpt_registration({"client_id": "oaiapp_saved", "email": "seller@example.com",
                                           "subject": "user-1", "host_id": "host-1"})
        params = await self.start()
        self.assertEqual(params["client_id"], "oaiapp_saved")
        self.assertEqual(params["login_hint"], "seller@example.com")
        self.assertNotIn("agent_name_hint", params)
        self.assertEqual(params["ext_agent_host_id"], "urn:uuid:host-1")
        await self.browser_returns(code="c", state=params["state"])
        self.assertEqual(keychain.get_chatgpt_tokens()["client_id"], "oaiapp_saved")

    async def test_another_account_on_the_same_registration_is_refused(self):
        keychain.set_chatgpt_registration({"client_id": "oaiapp_saved", "subject": "someone-else", "host_id": "h"})
        params = await self.start()
        page = await self.browser_returns(code="c", state=params["state"])
        self.assertIn("different ChatGPT account", page)
        self.assertIsNone(keychain.get_chatgpt_tokens())

    async def test_sign_in_without_token_sharing_saves_nothing(self):
        self.scope = "openid profile email offline_access"
        params = await self.start()
        page = await self.browser_returns(code="c", state=params["state"], client_id=self.issued_client)
        self.assertIn("token sharing is off", page)
        self.assertIn("token sharing is off", oauth.login_error())
        self.assertFalse(oauth.chatgpt_signed_in())
        self.assertFalse(keychain.get_chatgpt_registration()["sharing_enabled"])

    async def test_an_id_token_for_another_sign_in_is_refused(self):
        self.nonce_override = "not-this-sign-in"
        params = await self.start()
        page = await self.browser_returns(code="c", state=params["state"], client_id=self.issued_client)
        self.assertIn("could not be verified", page)
        self.assertIsNone(keychain.get_chatgpt_tokens())

    async def test_wrong_state_and_declines_are_reported(self):
        for query, message in (
            ({"code": "c", "state": "forged", "client_id": "oaiapp_x"}, "could not be verified"),
            ({"error": "access_denied"}, "declined"),
        ):
            with self.subTest(message=message):
                params = await self.start()
                query = {**query, **({"state": params["state"]} if "error" in query else {})}
                page = await self.browser_returns(**query)
                self.assertIn(message, page)
                self.assertIsNone(keychain.get_chatgpt_tokens())

    async def test_the_callback_works_once(self):
        params = await self.start()
        await self.browser_returns(code="c", state=params["state"], client_id=self.issued_client)
        self.assertEqual(len(self.token_requests()), 1)
        again = await self.browser_returns(code="c2", state=params["state"], client_id=self.issued_client)
        self.assertEqual(again, "refused")
        self.assertEqual(len(self.token_requests()), 1)

    async def test_cancel_closes_the_listener(self):
        params = await self.start()
        await oauth.cancel_login()
        await asyncio.sleep(0.2)
        with self.assertRaises(OSError):
            await asyncio.to_thread(urllib.request.urlopen, params["redirect_uri"], None, 2)

    async def test_discovery_off_openai_is_refused(self):
        with patch.dict(DISCOVERY, {"token_endpoint": "https://evil.example/token"}):
            with self.assertRaisesRegex(RuntimeError, "could not be verified"):
                await oauth.start_login()

    async def test_sign_out_revokes_the_refresh_token(self):
        keychain.set_chatgpt_tokens(saved_record())
        self.assertTrue(await oauth.logout())
        revoke = [r for r in self.requests if str(r.url) == DISCOVERY["revocation_endpoint"]]
        self.assertEqual(parse_qs(revoke[0].content.decode())["token"], ["old-refresh"])
        self.assertIsNone(keychain.get_chatgpt_tokens())


if __name__ == "__main__":
    unittest.main()
