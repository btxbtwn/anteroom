"""Provider usage normalization, isolation, and persistence regressions."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from vendoo_studio.services import model_usage
from vendoo_studio.providers.structured import repair_schema
from vendoo_studio.services.listing_generate import collect_provider_text


def test_openai_cached_tokens_are_not_counted_twice_and_secrets_are_ignored():
    with model_usage.capture() as calls:
        with model_usage.observe("openai", "fixture") as call:
            call.usage({"input_tokens": 100, "input_tokens_details": {"cached_tokens": 60},
                        "output_tokens": 20, "output_tokens_details": {"reasoning_tokens": 5},
                        "api_key": "do-not-record", "prompt": "private prompt"})
    assert calls[0]["input_tokens"] == 100
    assert calls[0]["cached_input_tokens"] == 60
    assert calls[0]["reasoning_tokens"] == 5
    assert calls[0]["cost_usd"] is None
    assert "do-not-record" not in json.dumps(calls)
    assert "private prompt" not in json.dumps(calls)


def test_anthropic_stream_combines_uncached_cached_and_output_usage():
    with model_usage.capture() as calls:
        with model_usage.observe("anthropic", "fixture") as call:
            call.usage({"input_tokens": 10, "cache_read_input_tokens": 60,
                        "cache_creation_input_tokens": 30}, anthropic=True)
            call.usage({"output_tokens": 20}, anthropic=True)
    assert calls[0]["input_tokens"] == 100
    assert calls[0]["output_tokens"] == 20
    assert calls[0]["cache_write_input_tokens"] == 30


def test_sdk_cost_is_explicitly_an_estimate_and_zero_is_valid():
    with model_usage.capture() as calls:
        with model_usage.observe("claude", "fixture") as call:
            call.usage({"input_tokens": 1}, anthropic=True, sdk_cost=0)
    assert calls[0]["cost_usd"] == 0
    assert calls[0]["cost_source"] == "sdk_estimate"


def test_unavailable_usage_is_unknown_and_failed_requests_keep_their_record():
    with model_usage.capture() as calls, pytest.raises(ValueError):
        with model_usage.observe("cursor", "fixture"):
            raise ValueError("private error not stored")
    assert calls[0]["outcome"] == "failed"
    assert calls[0]["input_tokens"] is None
    assert calls[0]["latency_ms"] >= 0
    assert "private error" not in json.dumps(calls)


@pytest.mark.parametrize("value", [True, -1, float("nan"), float("inf"), "100"])
def test_invalid_numeric_usage_is_never_persisted(value):
    with model_usage.capture() as calls:
        with model_usage.observe("fixture", "fixture") as call:
            call.usage({"input_tokens": value, "cost": value})
    assert calls[0]["input_tokens"] is None
    assert calls[0]["cost_usd"] is None


def test_parallel_cases_keep_separate_usage_records():
    async def one(label):
        with model_usage.capture() as calls:
            with model_usage.observe(label, "fixture"):
                await asyncio.sleep(0)
        return calls
    async def run():
        return await asyncio.gather(one("a"), one("b"))
    a, b = asyncio.run(run())
    assert [call["provider"] for call in a] == ["a"]
    assert [call["provider"] for call in b] == ["b"]
    assert model_usage.current() is None


def test_repair_schema_forbids_unknown_paths_and_extra_properties():
    schema = repair_schema(["/price"])
    operation = schema["$defs"]["RepairOperation"]
    assert operation["properties"]["path"]["enum"] == ["/price"]
    assert operation["additionalProperties"] is False
    assert schema["additionalProperties"] is False
    assert set(operation["required"]) == {"op", "path", "value"}


def test_collect_uses_native_schema_only_when_requested():
    class Provider:
        async def structured_chat(self, messages, schema):
            assert schema == {"type": "object"}
            yield '{"patches":[]}'
        async def chat(self, messages, stream=False):
            yield "ordinary chat"
    provider = Provider()
    assert asyncio.run(collect_provider_text(provider, [], output_schema={"type": "object"})) == '{"patches":[]}'
    assert asyncio.run(collect_provider_text(provider, [])) == "ordinary chat"


def test_an_unsupported_schema_request_fails_closed_instead_of_retrying_plain_text():
    class Provider:
        async def structured_chat(self, messages, schema):
            raise RuntimeError("Schema rejected")
            yield
        chat = AsyncMock()
    provider = Provider()
    with pytest.raises(RuntimeError, match="Schema rejected"):
        asyncio.run(collect_provider_text(provider, [], output_schema={"type": "object"}))
    provider.chat.assert_not_called()


def test_call_metrics_survive_stream_and_finish_persistence(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from vendoo_studio.database import Base, load_models
    from vendoo_studio.services import generation_timing

    load_models()
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(generation_timing, "SessionLocal", lambda: Session(engine))
    try:
        with model_usage.capture() as calls:
            with model_usage.observe("fixture", "fixture") as call:
                call.usage({"input_tokens": 10, "output_tokens": 2})
            timing_id = generation_timing.record_run("fixture", {"stream": 1}, total_sec=1,
                                                    outcome="preparing", calls=calls)
            with model_usage.observe("fixture", "fixture") as call:
                call.usage({"input_tokens": 4, "output_tokens": 1})
            generation_timing.add_stages(timing_id, {"finish": 1}, total_sec=2, outcome="done", calls=calls)
        with Session(engine) as db:
            [saved] = generation_timing.for_conversation(db, "fixture")
            assert saved["outcome"] == "done"
            assert [call["input_tokens"] for call in saved["calls"]] == [10, 4]
    finally:
        engine.dispose()
