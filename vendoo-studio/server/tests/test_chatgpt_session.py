from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from vendoo_studio.services import chatgpt_oauth as oauth


class ChatGPTSessionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client_type = httpx.AsyncClient
        self.saved = {"access_token": "old-access", "refresh_token": "old-refresh", "expires_at": 0}
        self.writes = Mock(side_effect=self.save)
        self.deletes = Mock(side_effect=self.delete)
        patches = [
            patch.object(oauth, "get_chatgpt_tokens", side_effect=lambda: self.saved),
            patch.object(oauth, "set_chatgpt_tokens", self.writes),
            patch.object(oauth, "delete_chatgpt_tokens", self.deletes),
            patch.object(oauth, "_refresh_lock", asyncio.Lock()),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def save(self, tokens):
        self.saved = tokens

    def delete(self):
        self.saved = None

    def client(self, handler):
        client = self.client_type(transport=httpx.MockTransport(handler))
        item = patch.object(oauth.httpx, "AsyncClient", return_value=client)
        item.start()
        self.addCleanup(item.stop)

    async def test_unexpired_session_does_not_need_the_network(self):
        self.saved["expires_at"] = time.time() + 3600
        with patch.object(oauth.httpx, "AsyncClient") as client:
            self.assertEqual(await oauth.refresh_chatgpt_tokens(), self.saved)
        client.assert_not_called()

    async def test_temporary_http_errors_keep_the_saved_login(self):
        for status, body in ((429, {"error": "rate_limited"}), (500, {"error": "invalid_grant"}),
                             (502, None), (400, {"error": "invalid_client"}), (403, None)):
            with self.subTest(status=status, body=body):
                self.client(lambda request, status=status, body=body: httpx.Response(
                    status, json=body, text="upstream unavailable" if body is None else None,
                ))
                with self.assertRaisesRegex(RuntimeError, "saved sign-in has been kept"):
                    await oauth.refresh_chatgpt_tokens()
                self.assertTrue(oauth.chatgpt_signed_in())
                self.deletes.assert_not_called()
                self.writes.assert_not_called()

    async def test_timeout_keeps_the_saved_login(self):
        def timeout(request):
            raise httpx.ReadTimeout("timed out", request=request)

        self.client(timeout)
        with self.assertRaises(httpx.ReadTimeout):
            await oauth.refresh_chatgpt_tokens()
        self.assertTrue(oauth.chatgpt_signed_in())
        self.deletes.assert_not_called()

    async def test_rejected_refresh_token_requires_sign_in(self):
        for error in ("invalid_grant", {"code": "refresh_token_reused"},
                      {"code": "refresh_token_expired"}, {"code": "refresh_token_invalidated"}):
            with self.subTest(error=error):
                self.saved = {"access_token": "old-access", "refresh_token": "old-refresh", "expires_at": 0}
                self.client(lambda request, error=error: httpx.Response(400, json={"error": error}))
                with self.assertRaisesRegex(RuntimeError, "Sign in again"):
                    await oauth.refresh_chatgpt_tokens()
                self.assertFalse(oauth.chatgpt_signed_in())

    async def test_concurrent_refreshes_rotate_once_and_save_the_new_token(self):
        for force in (False, True):
            with self.subTest(force=force):
                self.saved["expires_at"] = 0
                self.writes.reset_mock()
                requests = []

                async def respond(request, requests=requests):
                    requests.append(request)
                    await asyncio.sleep(0)
                    return httpx.Response(200, json={
                        "access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600,
                    })

                self.client(respond)
                results = await asyncio.gather(*(oauth.refresh_chatgpt_tokens(force=force) for _ in range(4)))
                self.assertEqual(len(requests), 1)
                self.assertEqual([result["refresh_token"] for result in results], ["new-refresh"] * 4)
                self.assertEqual(self.saved["access_token"], "new-access")
                self.writes.assert_called_once()
                self.deletes.assert_not_called()

    async def test_logout_during_refresh_cannot_restore_the_session(self):
        async def respond(request):
            oauth.logout()
            return httpx.Response(200, json={"access_token": "new", "refresh_token": "new"})

        self.client(respond)
        self.assertIsNone(await oauth.refresh_chatgpt_tokens())
        self.assertFalse(oauth.chatgpt_signed_in())
        self.writes.assert_not_called()

    async def test_old_refresh_failure_cannot_delete_a_new_login(self):
        async def respond(request):
            self.saved = {"access_token": "new-login", "refresh_token": "new-login"}
            return httpx.Response(400, json={"error": "invalid_grant"})

        self.client(respond)
        self.assertEqual((await oauth.refresh_chatgpt_tokens())["access_token"], "new-login")
        self.deletes.assert_not_called()

    async def test_malformed_token_response_keeps_the_saved_login(self):
        self.client(lambda request: httpx.Response(200, json={"expires_in": 3600}))
        with self.assertRaisesRegex(RuntimeError, "access token"):
            await oauth.refresh_chatgpt_tokens()
        self.deletes.assert_not_called()
        self.writes.assert_not_called()

    async def test_login_reports_a_failed_keychain_save_even_with_tokens_in_memory(self):
        from vendoo_studio.routes.settings import _chatgpt_status

        pending = oauth.PendingLogin("https://example.com", "user-code", "device-id", 1)
        self.client(lambda request: httpx.Response(200, json={
            "authorization_code": "code", "code_verifier": "verifier",
        }))
        with (
            patch.object(oauth, "_pending", pending),
            patch.object(oauth, "_exchange_code", AsyncMock(return_value=self.saved)),
            patch.object(oauth, "set_chatgpt_tokens", side_effect=RuntimeError("Could not save the ChatGPT sign-in")),
        ):
            await oauth._complete_login(pending)
            self.assertTrue(pending.done)
            self.assertEqual(_chatgpt_status().error, "Could not save the ChatGPT sign-in")
