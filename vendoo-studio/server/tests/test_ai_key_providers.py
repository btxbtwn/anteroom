from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from vendoo_studio.providers.anthropic_api import AnthropicAPIProvider, to_messages
from vendoo_studio.providers.chat_completions import ChatCompletionsProvider, OpenRouterProvider
from vendoo_studio.providers.openai_api import OpenAIAPIProvider
from vendoo_studio.providers.xiaomi_mimo import StreamChunk
from vendoo_studio.services import keychain, openrouter_oauth, user_settings
from vendoo_studio.services.ai_key_catalog import KEY_PROVIDERS


@pytest.fixture
def fake_http(monkeypatch):
    """Answer every outgoing request with ``handler``; returns the request log."""
    calls: list[httpx.Request] = []
    state: dict = {"handler": None}
    real = httpx.AsyncClient

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return state["handler"](request)

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(transport)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)

    def use(handler):
        state["handler"] = handler
        return calls

    return use


@pytest.fixture
def clean_settings():
    def reset(payload: dict) -> None:
        for key in (user_settings.LISTING_PROVIDER_KEY, user_settings.PHOTO_PROVIDER_KEY, user_settings.KEY_PROVIDER_MODELS_KEY):
            payload.pop(key, None)

    user_settings.update_settings(reset)
    yield
    user_settings.update_settings(reset)


def run(coro):
    return asyncio.run(coro)


async def collect(stream) -> list:
    return [item async for item in stream]


def sse(*events: dict) -> bytes:
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode() + b"data: [DONE]\n\n"


IMAGE_TURN = [{"role": "user", "content": [{"type": "text", "text": "hi"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGk="}}]}]


def test_keys_share_one_keychain_item():
    keychain.set_ai_key("openai", "sk-one")
    keychain.set_ai_key("gemini", "AIza-two")
    assert keychain.get_ai_keys() == {"openai": "sk-one", "gemini": "AIza-two"}
    keychain.delete_ai_key("openai")
    assert keychain.get_ai_key("openai") is None
    assert keychain.get_ai_key("gemini") == "AIza-two"
    keychain.delete_ai_key("gemini")
    assert keychain.get_ai_keys() == {}


def test_listing_provider_uses_the_seller_key(clean_settings):
    from vendoo_studio.services.listing_provider import get_listing_provider, provider_is_configured

    keychain.set_ai_key("anthropic", "sk-ant-x")
    user_settings.set_listing_provider_order("anthropic", "none")
    provider = get_listing_provider()
    assert isinstance(provider, AnthropicAPIProvider)
    assert provider.listing_model == KEY_PROVIDERS["anthropic"].listing_model
    assert provider_is_configured()

    user_settings.set_key_provider_models("anthropic", listing_model="claude-opus-5-5")
    assert get_listing_provider().listing_model == "claude-opus-5-5"


def test_any_saved_key_is_used_when_the_order_is_not_ready(clean_settings):
    from vendoo_studio.services.listing_provider import get_listing_provider

    keychain.set_ai_key("mistral", "m-key")
    provider = get_listing_provider()
    assert isinstance(provider, ChatCompletionsProvider)
    assert provider.name == "mistral"


def test_unknown_provider_choice_is_rejected(clean_settings):
    with pytest.raises(ValueError):
        user_settings.set_listing_provider_order("bogus")
    assert user_settings.set_listing_provider_order("xai", "openai") == {"primary": "xai", "fallback": "openai"}


def test_chat_completions_streams_thinking_and_text(fake_http):
    calls = fake_http(lambda request: httpx.Response(200, content=sse(
        {"choices": [{"delta": {"reasoning_content": "hmm"}}]},
        {"choices": [{"delta": {"content": "Hello"}}]},
    )))
    provider = ChatCompletionsProvider(KEY_PROVIDERS["xai"], "xai-key", "grok-vision", "grok-listing")
    chunks = run(collect(provider.chat([{"role": "user", "content": "hi"}])))
    assert chunks == [StreamChunk("hmm", "thinking"), StreamChunk("Hello", "content")]
    body = json.loads(calls[0].content)
    assert calls[0].url == "https://api.x.ai/v1/chat/completions"
    assert calls[0].headers["authorization"] == "Bearer xai-key"
    assert body["model"] == "grok-listing" and body["stream"] is True
    assert "temperature" not in body and "max_tokens" not in body


def test_chat_with_photos_uses_the_vision_model(fake_http):
    calls = fake_http(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "seen"}}]}))
    provider = ChatCompletionsProvider(KEY_PROVIDERS["gemini"], "g", "vision-m", "listing-m")
    assert run(collect(provider.chat(IMAGE_TURN, stream=False))) == ["seen"]
    assert json.loads(calls[0].content)["model"] == "vision-m"


def test_errors_name_the_provider(fake_http):
    fake_http(lambda request: httpx.Response(401, json=[{"error": {"message": "API key not valid"}}]))
    provider = ChatCompletionsProvider(KEY_PROVIDERS["gemini"], "bad", "m", "m")
    with pytest.raises(RuntimeError, match="Google Gemini HTTP 401: API key not valid"):
        run(provider.test_connection())


def test_photo_analysis_parses_evidence(fake_http, tmp_path):
    from PIL import Image

    photo = tmp_path / "item.jpg"
    Image.new("RGB", (8, 8), "red").save(photo)
    reply = 'Here you go: {"brand": {"value": "Levi\'s"}}'
    calls = fake_http(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": reply}}]}))
    provider = ChatCompletionsProvider(KEY_PROVIDERS["mistral"], "k", "vision-m", "listing-m")
    result = run(provider.analyze_photos([str(photo)], notes="Tag says 32x30"))
    assert result == {"evidence": {"brand": {"value": "Levi's"}}}
    body = json.loads(calls[0].content)
    assert body["model"] == "vision-m"
    assert "Tag says 32x30" in body["messages"][0]["content"]


def test_openrouter_web_search_reads_nested_citations(fake_http):
    payload = {"choices": [{"message": {
        "content": "Sold for about $40.",
        "annotations": [{"type": "url_citation", "url_citation": {"url": "https://ebay.com/itm/1", "title": "Levi's 501", "content": "Sold $40"}}],
    }}]}
    calls = fake_http(lambda request: httpx.Response(200, json=payload))
    provider = OpenRouterProvider(KEY_PROVIDERS["openrouter"], "sk-or", "m", "m")
    result = run(provider.web_search([{"role": "user", "content": "comps"}]))
    assert result == {"answer": "Sold for about $40.", "sources": [{"url": "https://ebay.com/itm/1", "title": "Levi's 501", "description": "Sold $40"}]}
    body = json.loads(calls[0].content)
    assert body["plugins"] == [{"id": "web"}]
    assert calls[0].headers["x-title"] == "Anteroom"


def test_models_without_web_search_fall_back_to_brave(clean_settings):
    from vendoo_studio.services.comp_research import model_search

    keychain.set_ai_key("gemini", "g")
    user_settings.set_listing_provider_order("gemini", "none")
    assert model_search() is None
    keychain.set_ai_key("openrouter", "o")
    user_settings.set_listing_provider_order("openrouter", "none")
    assert model_search().label == "OpenRouter"


def test_openai_key_calls_the_public_responses_api(fake_http):
    calls = fake_http(lambda request: httpx.Response(200, content=sse(
        {"type": "response.output_text.delta", "delta": "Listing"},
    )))
    provider = OpenAIAPIProvider("sk-test", "gpt-6-luna", "gpt-6.1-sol")
    chunks = run(collect(provider.chat([{"role": "system", "content": "rules"}, {"role": "user", "content": "go"}])))
    assert chunks == [StreamChunk("Listing", "content")]
    assert calls[0].url == "https://api.openai.com/v1/responses"
    assert calls[0].headers["authorization"] == "Bearer sk-test"
    body = json.loads(calls[0].content)
    assert body["model"] == "gpt-6.1-sol"
    assert body["instructions"] == "rules"
    assert "summary" not in body["reasoning"]


def test_openai_errors_say_openai(fake_http):
    fake_http(lambda request: httpx.Response(401, json={"error": {"message": "Incorrect API key provided"}}))
    with pytest.raises(RuntimeError, match="OpenAI HTTP 401: Incorrect API key provided"):
        run(collect(OpenAIAPIProvider("bad", "m", "m").chat([{"role": "user", "content": "x"}], stream=False)))


def test_anthropic_messages_split_system_and_images():
    system, turns = to_messages([{"role": "system", "content": "Rules"}, {"role": "assistant", "content": "Earlier"}, *IMAGE_TURN])
    assert system == "Rules"
    assert turns[0] == {"role": "user", "content": [{"type": "text", "text": "Continue."}]}
    assert turns[1] == {"role": "assistant", "content": [{"type": "text", "text": "Earlier"}]}
    assert turns[2]["content"][1] == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "aGk="}}


def test_anthropic_streams_text(fake_http):
    events = (
        {"type": "message_start"},
        {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "plan"}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}},
    )
    calls = fake_http(lambda request: httpx.Response(200, content="".join(f"event: x\ndata: {json.dumps(e)}\n\n" for e in events).encode()))
    provider = AnthropicAPIProvider("sk-ant", "claude-haiku-5-5", "claude-sonnet-5-5")
    assert run(collect(provider.chat([{"role": "user", "content": "hi"}]))) == [StreamChunk("plan", "thinking"), StreamChunk("Hi", "content")]
    assert calls[0].headers["x-api-key"] == "sk-ant"
    body = json.loads(calls[0].content)
    assert body["model"] == "claude-sonnet-5-5" and body["max_tokens"] > 0 and "temperature" not in body


def test_anthropic_web_search_resumes_paused_turns(fake_http):
    replies = iter([
        {"stop_reason": "pause_turn", "content": [{"type": "web_search_tool_result", "content": [{"type": "web_search_result", "url": "https://a.example", "title": "A"}]}]},
        {"stop_reason": "end_turn", "content": [{"type": "text", "text": "About $40", "citations": [{"type": "web_search_result_location", "url": "https://b.example", "title": "B", "cited_text": "$40"}]}]},
    ])
    calls = fake_http(lambda request: httpx.Response(200, json=next(replies)))
    result = run(AnthropicAPIProvider("k", "m", "m").web_search([{"role": "user", "content": "comps"}]))
    assert result["answer"] == "About $40"
    assert [source["url"] for source in result["sources"]] == ["https://a.example", "https://b.example"]
    assert len(calls) == 2
    assert json.loads(calls[1].content)["messages"][-1]["role"] == "assistant"


def test_openrouter_sign_in_exchanges_the_code_for_a_key(fake_http):
    calls = fake_http(lambda request: httpx.Response(200, json={"key": "sk-or-new"}))
    url = urlparse(openrouter_oauth.start_sign_in())
    query = parse_qs(url.query)
    callback = urlparse(query["callback_url"][0])
    assert callback.netloc.startswith("localhost:")
    state = parse_qs(callback.query)["state"][0]

    run(openrouter_oauth.finish_sign_in("the-code", state))
    body = json.loads(calls[0].content)
    digest = base64.urlsafe_b64encode(hashlib.sha256(body["code_verifier"].encode()).digest()).rstrip(b"=").decode()
    assert digest == query["code_challenge"][0]
    assert body["code"] == "the-code"
    assert keychain.get_ai_key("openrouter") == "sk-or-new"

    with pytest.raises(RuntimeError, match="expired"):
        run(openrouter_oauth.finish_sign_in("the-code", state))


def test_routes_save_mask_and_delete_keys(clean_settings):
    from vendoo_studio.main import app

    client = TestClient(app)
    assert client.put("/api/settings/ai-keys/openai", json={"api_key": " Bearer sk-abcdefghijklmnop "}).status_code == 200
    assert keychain.get_ai_key("openai") == "sk-abcdefghijklmnop"
    listed = {row["id"]: row for row in client.get("/api/settings/ai-keys").json()["providers"]}
    assert set(listed) == set(KEY_PROVIDERS)
    assert listed["openai"]["masked_key"] == "sk-abcde...mnop"
    assert listed["openrouter"]["sign_in"] is True
    assert listed["gemini"]["masked_key"] is None

    assert client.put("/api/settings/ai-keys/openai/models", json={"listing_model": "gpt-6-astra"}).json()["listing_model"] == "gpt-6-astra"
    assert client.put("/api/settings/ai-keys/openai/models", json={}).status_code == 400
    assert client.put("/api/settings/provider/preferred", json={"primary": "openai", "fallback": "none"}).status_code == 200
    status = client.get("/api/settings/provider").json()
    assert status["provider"] == "openai" and status["listing_model"] == "gpt-6-astra"

    assert client.put("/api/settings/provider/photos", json={"choice": "nope"}).status_code == 422
    assert client.put("/api/settings/ai-keys/nope", json={"api_key": "x"}).status_code == 404
    assert client.delete("/api/settings/ai-keys/openai").status_code == 200
    assert keychain.get_ai_key("openai") is None
    assert client.post("/api/settings/ai-keys/openai/test").status_code == 400


def test_openrouter_callback_page_reports_failure():
    from vendoo_studio.main import app

    page = TestClient(app).get("/api/settings/ai-keys/openrouter/callback", params={"code": "c", "state": "unknown"})
    assert page.status_code == 200
    assert "did not finish" in page.text and "expired" in page.text


def test_frontend_lists_the_same_key_providers():
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "src" / "components" / "keyProviders.ts").read_text(encoding="utf-8")
    listed = re.search(r"KEY_PROVIDER_IDS: readonly KeyProviderId\[\] = \[([^\]]*)\]", source).group(1)
    assert tuple(re.findall(r'"([a-z]+)"', listed)) == tuple(KEY_PROVIDERS)


def test_openrouter_models_use_their_names_and_offer_reasoning(fake_http, clean_settings):
    from vendoo_studio.main import app

    keychain.set_ai_key("openrouter", "sk-or")
    user_settings.set_key_provider_models("openrouter", listing_model="openai/gpt-5")
    fake_http(lambda request: httpx.Response(200, json={"data": [
        {
            "id": "openrouter/auto",
            "name": "Auto Router",
            "supported_parameters": ["reasoning", "reasoning_effort"],
            "reasoning": None,
        },
        {
            "id": "openai/gpt-5",
            "name": "OpenAI: GPT-5",
            "supported_parameters": ["reasoning", "reasoning_effort"],
            "reasoning": {"supported_efforts": ["high", "medium", "low", "minimal"], "default_effort": "medium"},
        },
    ]}))
    client = TestClient(app)
    payload = client.get("/api/settings/ai-keys/openrouter/models").json()
    assert payload["labels"] == {"openrouter/auto": "Auto Router", "openai/gpt-5": "OpenAI: GPT-5"}
    assert payload["reasoning"] == {
        "options": [
            {"value": "default", "label": "Default"},
            {"value": "high", "label": "High"},
            {"value": "medium", "label": "Medium"},
            {"value": "low", "label": "Low"},
            {"value": "minimal", "label": "Minimal"},
        ],
        "value": "default",
    }

    assert client.put("/api/settings/ai-keys/openrouter/models", json={"reasoning_effort": "high"}).status_code == 200
    assert user_settings.resolved_key_provider_reasoning("openrouter") == "high"
    payload = client.get("/api/settings/ai-keys/openrouter/models").json()
    assert payload["reasoning"]["value"] == "high"

    assert client.put("/api/settings/ai-keys/openrouter/models", json={"reasoning_effort": "default"}).status_code == 200
    assert user_settings.resolved_key_provider_reasoning("openrouter") is None
    assert client.put("/api/settings/ai-keys/openai/models", json={"reasoning_effort": "high"}).status_code == 400
    keychain.delete_ai_key("openrouter")


def test_openrouter_reasoning_is_sent_only_for_the_listing_model(fake_http):
    calls = fake_http(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}))
    provider = OpenRouterProvider(KEY_PROVIDERS["openrouter"], "sk-or", "vision-m", "listing-m", "high")
    assert run(collect(provider.chat([{"role": "user", "content": "hi"}], stream=False))) == ["ok"]
    assert json.loads(calls[0].content)["reasoning"] == {"effort": "high"}

    calls.clear()
    assert run(collect(provider.chat(IMAGE_TURN, stream=False))) == ["ok"]
    body = json.loads(calls[0].content)
    assert body["model"] == "vision-m" and "reasoning" not in body
