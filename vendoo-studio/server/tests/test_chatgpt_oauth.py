from __future__ import annotations

import base64
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from vendoo_studio.services.comp_research import comps_search_messages
from vendoo_studio.providers.chatgpt_codex import (
    ChatGPTCodexProvider,
    _messages_to_input,
    _responses_text,
    _responses_thinking,
    clamp_reasoning_effort,
    resolved_chatgpt_models,
    resolved_chatgpt_reasoning,
    supported_reasoning_efforts,
    visible_model_slugs,
    web_search_answer,
    web_search_sources,
)
from vendoo_studio.services.chatgpt_oauth import profile_from_tokens


def _jwt(payload: dict) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"aaa.{raw}.sig"


class ChatGPTOAuthTest(unittest.TestCase):
    def test_profile_from_the_saved_connection(self):
        token = _jwt({"https://api.openai.com/auth": {"chatgpt_plan_type": "plus"}})
        profile = profile_from_tokens({"id_token": token, "email": "seller@example.com"})
        self.assertEqual(profile, {"email": "seller@example.com", "plan": "plus"})
        self.assertEqual(profile_from_tokens(None), {"email": None, "plan": None})

    def test_responses_text_delta(self):
        self.assertEqual(
            _responses_text({"type": "response.output_text.delta", "delta": "hello"}),
            "hello",
        )

    def test_responses_thinking_delta(self):
        self.assertEqual(
            _responses_thinking({"type": "response.reasoning_summary_text.delta", "delta": "checking the photos"}),
            "checking the photos",
        )
        self.assertEqual(
            _responses_thinking({"type": "response.output_text.delta", "delta": "hello"}),
            "",
        )

    def test_messages_to_input_splits_system(self):
        instructions, items = _messages_to_input([
            {"role": "system", "content": "Be careful."},
            {"role": "user", "content": [{"type": "text", "text": "Look"}, {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,xx"}}]},
        ])
        self.assertEqual(instructions, "Be careful.")
        self.assertEqual(items[0]["role"], "user")
        self.assertEqual(items[0]["content"][0]["type"], "input_text")
        self.assertEqual(items[0]["content"][1]["type"], "input_image")

    def test_oversized_instructions_preserve_all_context_in_order(self):
        from vendoo_studio.providers.chatgpt_codex import MAX_INSTRUCTION_CHARS

        text = "rule\n" * (MAX_INSTRUCTION_CHARS // 5 * 3)
        instructions, items = _messages_to_input([
            {"role": "system", "content": text},
            {"role": "system", "content": "Never publish."},
            {"role": "user", "content": "Generate a draft."},
        ])
        chunks = [instructions] + [item["content"][0]["text"] for item in items[:-1]]
        self.assertTrue(all(len(chunk) <= MAX_INSTRUCTION_CHARS for chunk in chunks))
        self.assertEqual("".join(chunks), text + "\n\nNever publish.")
        self.assertTrue(all(item["role"] == "developer" for item in items[:-1]))
        self.assertEqual(items[-1]["role"], "user")

        with patch("vendoo_studio.providers.chatgpt_codex.get_chatgpt_models", return_value={}):
            provider = ChatGPTCodexProvider()
        payload = provider._payload([
            {"role": "system", "content": text},
            {"role": "system", "content": "Never publish."},
            {"role": "user", "content": "Generate a draft."},
        ], "gpt-6.1-sol", True)
        self.assertNotIn("instructions", payload)
        self.assertEqual([item["role"] for item in payload["input"]], ["developer"] * len(chunks) + ["user"])
        self.assertEqual("".join(item["content"][0]["text"] for item in payload["input"][:-1]), text + "\n\nNever publish.")

    def test_messages_to_input_uses_output_text_for_assistant(self):
        instructions, items = _messages_to_input([
            {"role": "system", "content": "Be careful."},
            {"role": "user", "content": "Fill empty fields."},
            {"role": "assistant", "content": '{"title":"Nike tee"}'},
            {"role": "user", "content": "SKU next"},
        ])
        self.assertEqual(instructions, "Be careful.")
        self.assertEqual([item["role"] for item in items], ["user", "assistant", "user"])
        self.assertEqual(items[0]["content"][0]["type"], "input_text")
        self.assertEqual(items[1]["content"][0]["type"], "output_text")
        self.assertEqual(items[1]["content"][0]["text"], '{"title":"Nike tee"}')
        self.assertEqual(items[2]["content"][0]["type"], "input_text")

    def test_visible_model_slugs_skip_hidden(self):
        slugs = visible_model_slugs({
            "models": [
                {"slug": "gpt-6-astra", "visibility": "list", "priority": 1},
                {"slug": "gpt-reserve", "visibility": "hide", "priority": 3},
                {"slug": "gpt-5.5", "visibility": "list", "priority": 12},
            ]
        })
        self.assertEqual(slugs, ["gpt-6-astra", "gpt-5.5"])


class ChatGPTModelChoiceTest(unittest.TestCase):
    def test_gpt_61_sol_reasoning_efforts(self):
        for model in ("gpt-6.1-sol", "gpt-6.1-sol-2026-09-30", "openai/gpt-6.1-sol", " GPT-6.1-SOL "):
            with self.subTest(model=model):
                self.assertEqual(supported_reasoning_efforts(model), ("low", "medium", "high", "xhigh", "max"))
                self.assertEqual(clamp_reasoning_effort("none", model), "low")
                self.assertEqual(clamp_reasoning_effort("minimal", model), "low")
                self.assertEqual(clamp_reasoning_effort("max", model), "max")

    def test_gpt_61_sol_payload_uses_supported_saved_effort(self):
        for saved, expected in (("none", "low"), ("medium", "medium"), ("max", "max")):
            with self.subTest(saved=saved):
                with patch(
                    "vendoo_studio.providers.chatgpt_codex.get_chatgpt_models",
                    return_value={"listing_model": "gpt-6.1-sol", "reasoning_effort": saved},
                ):
                    provider = ChatGPTCodexProvider()
                payload = provider._payload([{"role": "user", "content": "Generate a listing"}], provider.listing_model, True)
                self.assertEqual(payload["reasoning"], {"effort": expected, "summary": "auto"})

    def test_resolved_models_use_saved_prefs(self):
        with patch(
            "vendoo_studio.providers.chatgpt_codex.get_chatgpt_models",
            return_value={"vision_model": "gpt-5.6-sol", "listing_model": "gpt-6-astra", "reasoning_effort": "high"},
        ):
            self.assertEqual(resolved_chatgpt_models(), ("gpt-5.6-sol", "gpt-6-astra"))
            self.assertEqual(resolved_chatgpt_reasoning(), "high")
            provider = ChatGPTCodexProvider()
        self.assertEqual(provider.vision_model, "gpt-5.6-sol")
        self.assertEqual(provider.listing_model, "gpt-6-astra")
        self.assertEqual(provider.reasoning_effort, "high")
        self.assertEqual(provider._payload([{"role": "user", "content": "hi"}], "gpt-5.5", True)["reasoning"]["effort"], "high")
        self.assertEqual(clamp_reasoning_effort("none", "gpt-6-astra"), "low")
        self.assertEqual(clamp_reasoning_effort("max", "gpt-5.5"), "xhigh")

    def test_search_payload_includes_web_search_tool(self):
        with patch(
            "vendoo_studio.providers.chatgpt_codex.get_chatgpt_models",
            return_value={"listing_model": "gpt-5.5", "reasoning_effort": "medium"},
        ):
            provider = ChatGPTCodexProvider()
        payload = provider._payload(
            [{"role": "user", "content": "Levi's shorts sold comps"}],
            "gpt-5.5",
            True,
            tools=[{"type": "web_search", "external_web_access": True}],
            tool_choice="required",
            include=["web_search_call.action.sources"],
            reasoning_effort="low",
        )
        self.assertEqual(payload["tools"], [{"type": "web_search", "external_web_access": True}])
        self.assertEqual(payload["tool_choice"], "required")
        self.assertEqual(payload["include"], ["web_search_call.action.sources"])
        self.assertEqual(payload["reasoning"]["effort"], "low")


class ChatGPTQuickChatTest(unittest.IsolatedAsyncioTestCase):
    async def test_repair_sends_low_for_gpt_61_sol_and_none_for_gpt_55(self):
        for model, expected_effort in (("gpt-6.1-sol", "low"), ("gpt-5.5", "none")):
            with self.subTest(model=model):
                captured = {}

                def respond(request, captured=captured):
                    captured.update(json.loads(request.content))
                    if "instructions" in captured or any(item["role"] == "system" for item in captured["input"]):
                        return httpx.Response(400, json={"error": {"message": "System messages are not allowed"}})
                    return httpx.Response(200, text=(
                        'data: {"type":"response.output_text.delta","delta":"{}"}\n\n'
                        'data: [DONE]\n\n'
                    ))

                client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
                with (
                    patch("vendoo_studio.providers.chatgpt_codex.get_chatgpt_models", return_value={
                        "listing_model": model, "reasoning_effort": "high",
                    }),
                    patch("vendoo_studio.providers.chatgpt_codex.httpx.AsyncClient", return_value=client),
                    patch.object(ChatGPTCodexProvider, "_headers", new=AsyncMock(return_value={})),
                ):
                    result = [text async for text in ChatGPTCodexProvider().quick_chat([
                        {"role": "system", "content": "Preserve listing facts."},
                        {"role": "user", "content": "Repair JSON"},
                    ])]
                self.assertEqual(result, ["{}"])
                self.assertEqual(captured["model"], model)
                self.assertEqual(captured["input"][0], {
                    "role": "developer", "type": "message",
                    "content": [{"type": "input_text", "text": "Preserve listing facts."}],
                })
                self.assertEqual(captured["reasoning"]["effort"], expected_effort)


class WebSearchExtractTest(unittest.TestCase):
    def test_reads_answer_citations_and_sources(self):
        output = [
            {
                "type": "web_search_call",
                "action": {
                    "sources": [{"title": "eBay sold", "url": "https://www.ebay.com/itm/1"}],
                },
            },
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Sold recently for $22.",
                        "annotations": [
                            {"type": "url_citation", "url": "https://www.ebay.com/itm/2", "title": "Another sold listing"},
                        ],
                    }
                ],
            },
        ]
        self.assertEqual(web_search_answer(output), "Sold recently for $22.")
        sources = web_search_sources(output)
        self.assertEqual(
            [item["url"] for item in sources],
            ["https://www.ebay.com/itm/1", "https://www.ebay.com/itm/2"],
        )


class WebSearchRequestTest(unittest.IsolatedAsyncioTestCase):
    """The comps prompt itself lives in comp_research; the provider sends it as is."""

    async def test_comp_search_requests_high_context_and_strict_evidence(self):
        with patch(
            "vendoo_studio.providers.chatgpt_codex.get_chatgpt_models",
            return_value={"listing_model": "gpt-5.5"},
        ):
            provider = ChatGPTCodexProvider()
        provider._web_search_once = AsyncMock(return_value={"answer": "{}", "sources": []})

        await provider.web_search(comps_search_messages("Patagonia Nano Puff jacket M blue sold comps"))

        messages = provider._web_search_once.await_args.args[0]
        tools = provider._web_search_once.await_args.kwargs["tools"]
        self.assertEqual(tools[0]["search_context_size"], "high")
        instructions = messages[0]["content"]
        self.assertIn("Require explicit evidence that the item sold", instructions)
        self.assertIn("exact listing URL you observed", instructions)


class ChatGPTCatalogFetchTest(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_lists_the_plans_models_from_the_public_api(self):
        captured: list[httpx.Request] = []

        def respond(request):
            captured.append(request)
            return httpx.Response(200, json={"models": [
                {"slug": "gpt-6.1-sol", "visibility": "list", "priority": 4},
                {"slug": "codex-auto-review", "visibility": "hide", "priority": 43},
            ]})

        from vendoo_studio.providers import chatgpt_codex

        real = httpx.AsyncClient
        with (
            patch("vendoo_studio.providers.chatgpt_codex.refresh_chatgpt_tokens", new=AsyncMock(return_value={"access_token": "tok"})),
            patch("vendoo_studio.providers.chatgpt_codex.httpx.AsyncClient", lambda *a, **k: real(transport=httpx.MockTransport(respond))),
        ):
            slugs = await chatgpt_codex.fetch_chatgpt_models(force=True)
        self.assertEqual(slugs, ["gpt-6.1-sol"])
        self.assertEqual(str(captured[0].url), "https://api.openai.com/v1/models")
        self.assertEqual(captured[0].headers["authorization"], "Bearer tok")

    def test_plain_api_model_lists_are_read_too(self):
        self.assertEqual(visible_model_slugs({"data": [{"id": "gpt-6-luna"}, {"id": "gpt-6.1-sol"}]}), ["gpt-6-luna", "gpt-6.1-sol"])


class ListingProviderPrefersChatGPTTest(unittest.TestCase):
    def test_prefers_chatgpt_when_signed_in(self):
        from vendoo_studio.providers.chatgpt_codex import ChatGPTCodexProvider
        from vendoo_studio.services import listing_provider

        with (
            patch(
                "vendoo_studio.services.listing_provider.get_listing_provider_order",
                return_value=("chatgpt", "mimo"),
            ),
            patch("vendoo_studio.services.listing_provider.chatgpt_signed_in", return_value=True),
            patch("vendoo_studio.services.listing_provider.get_api_key", return_value="sk-test"),
            patch("vendoo_studio.providers.chatgpt_codex.get_chatgpt_models", return_value={}),
        ):
            provider = listing_provider.get_listing_provider()
        self.assertIsInstance(provider, ChatGPTCodexProvider)

    def test_uses_mimo_when_primary(self):
        from vendoo_studio.providers.xiaomi_mimo import MiMoProvider
        from vendoo_studio.services import listing_provider

        with (
            patch(
                "vendoo_studio.services.listing_provider.get_listing_provider_order",
                return_value=("mimo", "chatgpt"),
            ),
            patch("vendoo_studio.services.listing_provider.chatgpt_signed_in", return_value=True),
            patch("vendoo_studio.services.listing_provider.get_api_key", return_value="sk-test"),
            patch("vendoo_studio.providers.chatgpt_codex.get_chatgpt_models", return_value={}),
        ):
            provider = listing_provider.get_listing_provider()
        self.assertIsInstance(provider, MiMoProvider)

    def test_falls_back_to_chatgpt_when_mimo_primary_but_missing_key(self):
        from vendoo_studio.providers.chatgpt_codex import ChatGPTCodexProvider
        from vendoo_studio.services import listing_provider

        with (
            patch(
                "vendoo_studio.services.listing_provider.get_listing_provider_order",
                return_value=("mimo", "chatgpt"),
            ),
            patch("vendoo_studio.services.listing_provider.chatgpt_signed_in", return_value=True),
            patch("vendoo_studio.services.listing_provider.get_api_key", return_value=None),
            patch("vendoo_studio.providers.chatgpt_codex.get_chatgpt_models", return_value={}),
        ):
            provider = listing_provider.get_listing_provider()
        self.assertIsInstance(provider, ChatGPTCodexProvider)

    def test_no_fallback_when_fallback_is_none(self):
        from vendoo_studio.services import listing_provider

        with (
            patch(
                "vendoo_studio.services.listing_provider.get_listing_provider_order",
                return_value=("mimo", "none"),
            ),
            patch("vendoo_studio.services.listing_provider.chatgpt_signed_in", return_value=True),
            patch("vendoo_studio.services.listing_provider.get_api_key", return_value=None),
            patch("vendoo_studio.providers.chatgpt_codex.get_chatgpt_models", return_value={}),
        ):
            self.assertIsNone(listing_provider.get_listing_provider())

    def test_falls_back_to_mimo_key(self):
        from vendoo_studio.providers.xiaomi_mimo import MiMoProvider
        from vendoo_studio.services import listing_provider

        with (
            patch(
                "vendoo_studio.services.listing_provider.get_listing_provider_order",
                return_value=("chatgpt", "mimo"),
            ),
            patch("vendoo_studio.services.listing_provider.chatgpt_signed_in", return_value=False),
            patch("vendoo_studio.services.listing_provider.get_api_key", return_value="sk-test"),
        ):
            provider = listing_provider.get_listing_provider()
        self.assertIsInstance(provider, MiMoProvider)

    def test_unconfigured_without_chatgpt_or_key(self):
        from vendoo_studio.services import listing_provider

        with (
            patch(
                "vendoo_studio.services.listing_provider.get_listing_provider_order",
                return_value=("chatgpt", "mimo"),
            ),
            patch("vendoo_studio.services.listing_provider.chatgpt_signed_in", return_value=False),
            patch("vendoo_studio.services.listing_provider.get_api_key", return_value=None),
            patch("vendoo_studio.services.listing_provider.get_cursor_api_key", return_value=None),
        ):
            self.assertIsNone(listing_provider.get_listing_provider())
            self.assertFalse(listing_provider.provider_is_configured())


class SettingsChatGPTRouteTest(unittest.TestCase):
    def test_provider_keeps_masked_mimo_key_while_chatgpt_is_signed_in(self):
        from fastapi.testclient import TestClient

        from vendoo_studio.main import app

        token = _jwt({
            "email": "seller@example.com",
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "acct_123",
                "chatgpt_plan_type": "plus",
            },
        })
        tokens = {"id_token": token, "access_token": "a", "refresh_token": "r"}
        with (
            patch("vendoo_studio.services.chatgpt_oauth.chatgpt_signed_in", return_value=True),
            patch("vendoo_studio.services.keychain.get_chatgpt_tokens", return_value=tokens),
            patch("vendoo_studio.services.keychain.get_api_key", return_value="sk-mimo-key-1234"),
            patch(
                "vendoo_studio.services.user_settings.get_listing_provider_order",
                return_value=("chatgpt", "mimo"),
            ),
            patch(
                "vendoo_studio.services.listing_provider.get_listing_provider_order",
                return_value=("chatgpt", "mimo"),
            ),
            patch("vendoo_studio.services.listing_provider.chatgpt_signed_in", return_value=True),
            patch("vendoo_studio.providers.chatgpt_codex.get_chatgpt_models", return_value={}),
        ):
            resp = TestClient(app).get("/api/settings/provider")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["provider"], "chatgpt")
        self.assertEqual(body["primary"], "chatgpt")
        self.assertEqual(body["fallback"], "mimo")
        self.assertTrue(body["configured"])
        self.assertEqual(body["listing_model"], "gpt-5.5")
        self.assertEqual(body["chatgpt"]["email"], "seller@example.com")
        self.assertEqual(body["chatgpt"]["plan"], "plus")
        self.assertEqual(body["masked_key"], "sk-mimo-...1234")

    def test_provider_uses_mimo_when_primary(self):
        from fastapi.testclient import TestClient

        from vendoo_studio.main import app

        token = _jwt({
            "email": "seller@example.com",
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "acct_123",
                "chatgpt_plan_type": "plus",
            },
        })
        tokens = {"id_token": token, "access_token": "a", "refresh_token": "r"}
        with (
            patch("vendoo_studio.services.chatgpt_oauth.chatgpt_signed_in", return_value=True),
            patch("vendoo_studio.services.keychain.get_chatgpt_tokens", return_value=tokens),
            patch("vendoo_studio.services.keychain.get_api_key", return_value="sk-mimo-key-1234"),
            patch(
                "vendoo_studio.services.user_settings.get_listing_provider_order",
                return_value=("mimo", "chatgpt"),
            ),
            patch(
                "vendoo_studio.services.listing_provider.get_listing_provider_order",
                return_value=("mimo", "chatgpt"),
            ),
            patch("vendoo_studio.services.listing_provider.chatgpt_signed_in", return_value=True),
            patch("vendoo_studio.services.listing_provider.get_api_key", return_value="sk-mimo-key-1234"),
            patch("vendoo_studio.providers.chatgpt_codex.get_chatgpt_models", return_value={}),
        ):
            resp = TestClient(app).get("/api/settings/provider")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["provider"], "xiaomi-mimo")
        self.assertEqual(body["primary"], "mimo")
        self.assertEqual(body["fallback"], "chatgpt")
        self.assertEqual(body["listing_model"], "mimo-v2.5-pro")
        self.assertTrue(body["chatgpt"]["signed_in"])

    def test_provider_hides_mimo_key_when_missing(self):
        from fastapi.testclient import TestClient

        from vendoo_studio.main import app

        with (
            patch("vendoo_studio.services.chatgpt_oauth.chatgpt_signed_in", return_value=True),
            patch("vendoo_studio.services.keychain.get_chatgpt_tokens", return_value={"access_token": "a", "refresh_token": "r"}),
            patch("vendoo_studio.services.keychain.get_api_key", return_value=None),
            patch(
                "vendoo_studio.services.user_settings.get_listing_provider_order",
                return_value=("chatgpt", "mimo"),
            ),
            patch(
                "vendoo_studio.services.listing_provider.get_listing_provider_order",
                return_value=("chatgpt", "mimo"),
            ),
            patch("vendoo_studio.services.listing_provider.chatgpt_signed_in", return_value=True),
            patch("vendoo_studio.services.listing_provider.get_api_key", return_value=None),
            patch("vendoo_studio.providers.chatgpt_codex.get_chatgpt_models", return_value={}),
        ):
            resp = TestClient(app).get("/api/settings/provider")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["masked_key"])

    def test_login_returns_the_sign_in_page(self):
        from fastapi.testclient import TestClient

        from vendoo_studio.main import app

        pending = {"authorize_url": "https://auth.openai.com/api/accounts/authorize?client_id=dynamic_agent_client"}
        with patch(
            "vendoo_studio.services.chatgpt_oauth.start_login",
            new=AsyncMock(return_value=pending),
        ):
            resp = TestClient(app).post("/api/settings/chatgpt/login")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), pending)


if __name__ == "__main__":
    unittest.main()


class ChatGPTSharedConnectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_calls_on_the_server_loop_share_one_client(self):
        from vendoo_studio.providers import chatgpt_codex

        chatgpt_codex.keep_connection_on_this_loop()
        try:
            async with chatgpt_codex._http_client() as first:
                pass
            async with chatgpt_codex._http_client() as second:
                pass
            self.assertIs(first, second)
            self.assertFalse(first.is_closed)
        finally:
            await chatgpt_codex.close_connection()
        self.assertTrue(first.is_closed)

    async def test_calls_on_another_loop_get_their_own_client(self):
        from vendoo_studio.providers import chatgpt_codex

        async with chatgpt_codex._http_client() as client:
            pass
        self.assertTrue(client.is_closed)
