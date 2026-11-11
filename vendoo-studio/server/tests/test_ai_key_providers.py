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
from vendoo_studio.services.provider_deadlines import unwrap


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
    provider = unwrap(get_listing_provider())
    assert isinstance(provider, AnthropicAPIProvider)
    assert provider.listing_model == KEY_PROVIDERS["anthropic"].listing_model
    assert provider_is_configured()

    user_settings.set_key_provider_models("anthropic", listing_model="claude-opus-5-5")
    assert get_listing_provider().listing_model == "claude-opus-5-5"


def test_any_saved_key_is_used_when_the_order_is_not_ready(clean_settings):
    from vendoo_studio.services.listing_provider import get_listing_provider

    keychain.set_ai_key("mistral", "m-key")
    provider = unwrap(get_listing_provider())
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


def test_reasoning_only_stream_is_retried_without_reasoning(fake_http):
    replies = iter([
        sse(
            {"choices": [{"delta": {"reasoning_content": "weighing it"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ),
        sse({"choices": [{"delta": {"content": "Listing"}}]}),
    ])
    calls = fake_http(lambda request: httpx.Response(200, content=next(replies)))
    provider = OpenRouterProvider(KEY_PROVIDERS["openrouter"], "sk-or", "vision-m", "listing-m")
    chunks = run(collect(provider.chat([{"role": "user", "content": "go"}])))
    assert chunks == [StreamChunk("weighing it", "thinking"), StreamChunk("Listing", "content")]
    assert len(calls) == 2
    assert json.loads(calls[1].content)["reasoning"] == {"enabled": False}


def test_reasoning_only_field_reply_is_retried_without_reasoning(fake_http):
    from vendoo_studio.services.listing_generate import collect_provider_text
    from vendoo_studio.services.native_field_fill import validated_values
    from vendoo_studio.services.option_lookup import OptionCatalog

    answer = '{"fields":{"ebay_specifics.Size":{"value":"XL","evidence":"size: XL"}}}'
    replies = iter([
        {"choices": [{"message": {"content": "", "reasoning": "Checking the size"}, "finish_reason": "length"}]},
        {"choices": [{"message": {"content": answer}, "finish_reason": "stop"}]},
    ])
    calls = fake_http(lambda request: httpx.Response(200, json=next(replies)))
    provider = OpenRouterProvider(KEY_PROVIDERS["openrouter"], "sk-or", "vision-m", "listing-m", "high")
    messages = [{"role": "user", "content": "Fill the size from evidence: size: XL"}]
    row = {"path": "ebay_specifics.Size", "options": ["S", "M", "L", "XL"], "selection_only": True}
    catalog = OptionCatalog({"ebay": {"fields": [row]}})
    text = run(collect_provider_text(provider, messages, option_catalog=catalog))
    assert text == answer
    assert validated_values(json.loads(text), {row["path"]: row}, evidence="size: XL")[row["path"]][0] == "XL"
    assert len(calls) == 2
    bodies = [json.loads(call.content) for call in calls]
    assert [body["reasoning"] for body in bodies] == [{"effort": "high"}, {"enabled": False}]
    assert all(body["model"] == "listing-m" and body["messages"] == messages for body in bodies)
    assert provider.reasoning_effort == "high"


def test_reasoning_only_plain_reply_retry_is_bounded(fake_http):
    calls = fake_http(lambda request: httpx.Response(200, json={
        "choices": [{"message": {"content": "", "reasoning": "Still thinking"}, "finish_reason": "length"}],
    }))
    provider = OpenRouterProvider(KEY_PROVIDERS["openrouter"], "sk-or", "vision-m", "listing-m")
    with pytest.raises(RuntimeError, match="Retried with reasoning off.*finish: length"):
        run(collect(provider.chat([{"role": "user", "content": "Fill the size"}], stream=False)))
    assert len(calls) == 2


@pytest.mark.parametrize("status", [401, 402])
def test_plain_reply_does_not_retry_key_or_credit_rejection(fake_http, status):
    calls = fake_http(lambda request: httpx.Response(status, json={"error": {"message": "Rejected"}}))
    provider = OpenRouterProvider(KEY_PROVIDERS["openrouter"], "sk-or", "vision-m", "listing-m")
    with pytest.raises(RuntimeError, match=f"HTTP {status}"):
        run(collect(provider.chat([{"role": "user", "content": "Fill the size"}], stream=False)))
    assert len(calls) == 1


def test_no_text_after_retry_names_the_finish_reason(fake_http):
    calls = fake_http(lambda request: httpx.Response(200, content=sse(
        {"choices": [{"delta": {"reasoning_content": "hmm"}, "finish_reason": "length"}]},
    )))
    provider = OpenRouterProvider(KEY_PROVIDERS["openrouter"], "sk-or", "vision-m", "listing-m")
    with pytest.raises(RuntimeError, match="streamed 3 characters of reasoning but no answer \\(finish: length\\)"):
        run(collect(provider.chat([{"role": "user", "content": "go"}])))
    assert len(calls) == 2


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
    from tests.test_photo_evidence_contract import evidence

    expected = evidence()
    expected["brand"] = {"value": "Levi's", "confidence": "high"}
    reply = "Here you go: " + json.dumps(expected)
    calls = fake_http(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": reply}}]}))
    provider = ChatCompletionsProvider(KEY_PROVIDERS["mistral"], "k", "vision-m", "listing-m")
    result = run(provider.analyze_photos([str(photo)], notes="Tag says 32x30"))
    assert result == {"evidence": expected}
    body = json.loads(calls[0].content)
    assert body["model"] == "vision-m"
    assert "Tag says 32x30" in body["messages"][0]["content"]


@pytest.mark.parametrize("nested", [True, False])
def test_openrouter_web_search_allows_repeated_searches_and_reads_citations(fake_http, nested):
    citation = {"url": "https://ebay.com/itm/1", "title": "Levi's 501", "content": "Sold $40"}
    annotation = {"type": "url_citation", **({"url_citation": citation} if nested else citation)}
    payload = {"output": [{"type": "message", "content": [{
        "type": "output_text", "text": "Sold for about $40.",
        "annotations": [annotation],
    }]}]}
    calls = fake_http(lambda request: httpx.Response(200, json=payload))
    provider = OpenRouterProvider(KEY_PROVIDERS["openrouter"], "sk-or", "vision-m", "deepseek/deepseek-v3.2")
    result = run(provider.web_search([{"role": "user", "content": "comps"}]))
    assert result == {"answer": "Sold for about $40.", "sources": [{"url": "https://ebay.com/itm/1", "title": "Levi's 501", "description": "Sold $40"}]}
    body = json.loads(calls[0].content)
    assert body["model"] == "deepseek/deepseek-v3.2"
    assert "plugins" not in body
    assert body["tools"] == [{"type": "openrouter:web_search"}]
    assert body["input"] == [{"role": "user", "content": "comps"}]
    assert "messages" not in body
    assert calls[0].url.path == "/api/v1/responses"
    assert calls[0].headers["x-title"] == "Anteroom"


@pytest.mark.parametrize("payload", [{"output": []}, {"output": [{"type": "reasoning", "summary": []}]}])
def test_openrouter_web_search_rejects_missing_answer_and_sources(fake_http, payload):
    fake_http(lambda request: httpx.Response(200, json=payload))
    provider = OpenRouterProvider(KEY_PROVIDERS["openrouter"], "sk-or", "m", "m")
    with pytest.raises(RuntimeError, match="OpenRouter web search returned no results"):
        run(provider.web_search([{"role": "user", "content": "comps"}]))


def test_openrouter_web_search_surfaces_search_access_errors(fake_http):
    calls = fake_http(lambda request: httpx.Response(403, json={"error": {"message": "Web search is disabled"}}))
    provider = OpenRouterProvider(KEY_PROVIDERS["openrouter"], "sk-or", "m", "m")
    with pytest.raises(RuntimeError, match="OpenRouter HTTP 403: Web search is disabled"):
        run(provider.web_search([{"role": "user", "content": "comps"}]))
    assert len(calls) == 1


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
    assert "instructions" not in body
    assert body["input"][0] == {
        "role": "developer", "type": "message",
        "content": [{"type": "input_text", "text": "rules"}],
    }
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


def test_responses_native_schema_and_usage_are_forwarded(fake_http):
    from vendoo_studio.services import model_usage
    schema = {"type": "object", "properties": {"patches": {"type": "array", "items": {"type": "string"}}},
              "required": ["patches"], "additionalProperties": False}
    def handler(request):
        return httpx.Response(200, content=sse(
            {"type": "response.output_text.delta", "delta": '{"patches":[]}'},
            {"type": "response.completed", "response": {"usage": {
                "input_tokens": 100, "input_tokens_details": {"cached_tokens": 60}, "output_tokens": 10}}},
        ))
    requests = fake_http(handler)
    provider = OpenAIAPIProvider("test-key", "fixture", "fixture")
    with model_usage.capture() as calls:
        result = run(collect(provider.structured_chat([{"role": "user", "content": "JSON"}], schema)))
    assert result
    body = json.loads(requests[0].content)
    assert body["text"]["format"] == {"type": "json_schema", "name": "listing_result", "strict": True, "schema": schema}
    assert calls[0]["input_tokens"] == 100
    assert calls[0]["cached_input_tokens"] == 60
    assert calls[0]["output_tokens"] == 10


def test_messages_native_schema_and_anthropic_usage(fake_http):
    from vendoo_studio.services import model_usage
    schema = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    def handler(request):
        return httpx.Response(200, json={"content": [{"type": "text", "text": "{}"}],
            "stop_reason": "end_turn", "usage": {"input_tokens": 5, "cache_read_input_tokens": 8,
            "cache_creation_input_tokens": 7, "output_tokens": 9}})
    requests = fake_http(handler)
    provider = AnthropicAPIProvider("test-key", "fixture", "fixture")
    with model_usage.capture() as calls:
        assert run(collect(provider.structured_chat([{"role": "user", "content": "JSON"}], schema))) == ["{}"]
    assert json.loads(requests[0].content)["output_config"]["format"]["schema"] == schema
    assert calls[0]["input_tokens"] == 20
    assert calls[0]["output_tokens"] == 9
    assert calls[0]["cost_usd"] is None


def test_chat_native_schema_and_reported_cost(fake_http):
    from vendoo_studio.services import model_usage
    schema = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    requests = fake_http(lambda request: httpx.Response(200, json={
        "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 5, "cost": 0.01}}))
    provider = ChatCompletionsProvider(KEY_PROVIDERS["gemini"], "test-key", "fixture", "fixture")
    with model_usage.capture() as calls:
        assert run(collect(provider.structured_chat([{"role": "user", "content": "JSON"}], schema))) == ["{}"]
    assert json.loads(requests[0].content)["response_format"]["json_schema"]["strict"] is True
    assert calls[0]["cost_source"] == "provider_reported"
    assert calls[0]["cost_usd"] == 0.01


def test_native_refusal_is_not_returned_as_an_empty_success(fake_http):
    from vendoo_studio.services import model_usage
    fake_http(lambda request: httpx.Response(200, content=sse({"type": "response.refusal.done", "refusal": "private text"})))
    provider = OpenAIAPIProvider("test-key", "fixture", "fixture")
    with model_usage.capture() as calls, pytest.raises(RuntimeError, match="did not complete"):
        run(collect(provider.structured_chat([{"role": "user", "content": "JSON"}], {"type": "object"})))
    assert calls[0]["outcome"] == "failed"
    assert "private text" not in json.dumps(calls)


def test_responses_search_requests_are_included_in_call_metrics(fake_http):
    from vendoo_studio.services import model_usage
    fake_http(lambda request: httpx.Response(200, content=sse(
        {"type": "response.output_item.done", "item": {"type": "message", "content": [
            {"type": "output_text", "text": "Synthetic sold comparable found."}]}},
        {"type": "response.completed", "response": {"usage": {"input_tokens": 12, "output_tokens": 3}}},
    )))
    provider = OpenAIAPIProvider("test-key", "fixture", "fixture")
    with model_usage.capture() as calls:
        result = run(provider.web_search([{"role": "user", "content": "Synthetic sold comps"}]))
    assert result["answer"] == "Synthetic sold comparable found."
    assert len(calls) == 1
    assert calls[0]["input_tokens"] == 12
    assert calls[0]["output_tokens"] == 3


def test_openrouter_routes_every_request_to_its_fastest_host(fake_http):
    # OpenRouter serves one model from several hosts; unsorted, a slow host
    # can take minutes over a reply a fast one returns in seconds.
    calls = fake_http(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}))
    provider = OpenRouterProvider(KEY_PROVIDERS["openrouter"], "sk-or", "vision-m", "listing-m", "high")
    run(collect(provider.quick_chat([{"role": "user", "content": "hi"}])))
    run(collect(provider.vision_chat(IMAGE_TURN)))
    run(collect(provider.structured_chat([{"role": "user", "content": "hi"}], {"type": "object"})))
    for call in calls:
        body = json.loads(call.content)
        assert body["provider"]["sort"] == "throughput"
        assert body["model"] in {"vision-m", "listing-m"}


def test_other_chat_completions_providers_get_no_openrouter_routing(fake_http):
    calls = fake_http(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}))
    provider = ChatCompletionsProvider(KEY_PROVIDERS["xai"], "xai-key", "grok-vision", "grok-listing")
    run(collect(provider.quick_chat([{"role": "user", "content": "hi"}])))
    assert "provider" not in json.loads(calls[0].content)


@pytest.fixture
def no_wait(monkeypatch):
    """Record retry waits instead of sleeping through them."""
    from vendoo_studio.providers import http_retry

    waits: list[float] = []

    async def sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(http_retry.asyncio, "sleep", sleep)
    return waits


@pytest.mark.parametrize("status", [408, 429, 500, 503, 529])
def test_anthropic_retries_a_busy_or_failed_request(fake_http, no_wait, status):
    replies = iter([
        httpx.Response(status, json={"error": {"message": "busy"}}, headers={"retry-after": "2"}),
        httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}),
    ])
    calls = fake_http(lambda request: next(replies))
    provider = AnthropicAPIProvider("k", "m", "m")
    assert run(collect(provider.quick_chat([{"role": "user", "content": "hi"}]))) == ["ok"]
    assert len(calls) == 2
    assert no_wait == [2.0]


def test_retries_stop_after_two_and_never_wait_out_a_long_retry_after(fake_http, no_wait):
    calls = fake_http(lambda request: httpx.Response(529, json={"error": {"message": "Overloaded"}}))
    provider = ChatCompletionsProvider(KEY_PROVIDERS["gemini"], "g", "m", "m")
    with pytest.raises(RuntimeError, match="HTTP 529: Overloaded"):
        run(collect(provider.quick_chat([{"role": "user", "content": "hi"}])))
    assert len(calls) == 3
    assert len(no_wait) == 2 and all(0 <= wait <= 8 for wait in no_wait)

    calls.clear()
    fake_http(lambda request: httpx.Response(429, json={"error": {"message": "Slow down"}}, headers={"retry-after": "600"}))
    with pytest.raises(RuntimeError, match="HTTP 429"):
        run(collect(provider.quick_chat([{"role": "user", "content": "hi"}])))
    assert len(calls) == 1


def test_connection_failures_are_retried(fake_http, no_wait):
    attempts = iter([httpx.ConnectError("refused"), None])

    def handler(request):
        failure = next(attempts)
        if failure is not None:
            raise failure
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    calls = fake_http(handler)
    provider = ChatCompletionsProvider(KEY_PROVIDERS["gemini"], "g", "m", "m")
    assert run(collect(provider.quick_chat([{"role": "user", "content": "hi"}]))) == ["ok"]
    assert len(calls) == 2


@pytest.mark.parametrize("provider", [
    lambda: AnthropicAPIProvider("k", "m", "m"),
    lambda: ChatCompletionsProvider(KEY_PROVIDERS["gemini"], "g", "m", "m"),
])
def test_a_rejected_stream_is_not_sent_again_without_streaming(fake_http, no_wait, provider):
    calls = fake_http(lambda request: httpx.Response(400, json={"error": {"message": "Bad request"}}))
    with pytest.raises(RuntimeError, match="HTTP 400: Bad request"):
        run(collect(provider().chat([{"role": "user", "content": "hi"}])))
    assert len(calls) == 1


def test_a_stream_that_breaks_before_answering_falls_back_to_one_plain_request(fake_http, no_wait):
    def handler(request):
        if json.loads(request.content).get("stream"):
            return httpx.Response(200, content=b'data: {"type": "error", "error": {"message": "stream broke"}}\n\n')
        return httpx.Response(200, json={"content": [{"type": "text", "text": "plain"}]})

    calls = fake_http(handler)
    assert run(collect(AnthropicAPIProvider("k", "m", "m").chat([{"role": "user", "content": "hi"}]))) == ["plain"]
    assert len(calls) == 2


def test_anthropic_requests_cache_their_prompt(fake_http):
    calls = fake_http(lambda request: httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]}))
    run(collect(AnthropicAPIProvider("k", "m", "m").quick_chat([{"role": "system", "content": "Rules"}, {"role": "user", "content": "hi"}])))
    body = json.loads(calls[0].content)
    assert body["cache_control"] == {"type": "ephemeral"}
    assert body["system"] == "Rules"


@pytest.mark.parametrize(("model", "tool"), [
    ("claude-sonnet-5-5", "web_search_20260209"),
    ("claude-opus-4-6", "web_search_20260209"),
    ("claude-fable-5-2", "web_search_20260209"),
    ("claude-sonnet-4-5-20250929", "web_search_20250305"),
    ("claude-opus-4-20250514", "web_search_20250305"),
    ("claude-haiku-5-5", "web_search_20250305"),
    ("claude-3-5-haiku-latest", "web_search_20250305"),
])
def test_anthropic_web_search_uses_the_newest_tool_the_model_takes(fake_http, model, tool):
    calls = fake_http(lambda request: httpx.Response(200, json={"stop_reason": "end_turn", "content": [{"type": "text", "text": "About $40"}]}))
    run(AnthropicAPIProvider("k", model, model).web_search([{"role": "user", "content": "comps"}]))
    assert json.loads(calls[0].content)["tools"][0]["type"] == tool


def test_openrouter_sends_strict_schemas_only_to_hosts_that_honour_them(fake_http):
    calls = fake_http(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}))
    provider = OpenRouterProvider(KEY_PROVIDERS["openrouter"], "sk-or", "openrouter/auto", "openrouter/auto")
    run(collect(provider.structured_chat([{"role": "user", "content": "JSON"}], {"type": "object"})))
    run(collect(provider.quick_chat([{"role": "user", "content": "hi"}])))
    assert json.loads(calls[0].content)["provider"] == {"sort": "throughput", "require_parameters": True}
    assert json.loads(calls[1].content)["provider"] == {"sort": "throughput"}
