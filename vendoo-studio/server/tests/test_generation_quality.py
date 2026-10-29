"""Quality regressions for scoped repair, durable context, and canonical retrieval."""
import asyncio
import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from vendoo_studio.database import Base, load_models
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services import chat_prompts, item_context, listing_generate, skill_formulas
from vendoo_studio.services.catalog_index import relevant_skill_rules
from vendoo_studio.services.generation_eval import evaluate_outputs
from vendoo_studio.services.text_retrieval import rank_texts

BASE = {
    "title": "Fixture M Casual T-Shirt Blue Regular",
    "description": "Casual blue cotton tee with a classic fit.\n\nMeasurements: See photos for measurements.",
    "brand": "Fixture", "size": "M", "price": 29, "condition": "Pre-Owned - Good",
    "category_path": "Clothing, Shoes & Accessories > Women > Women's Clothing > Tops",
    "department": "Women", "weight_lb": 0, "weight_oz": 8, "package_dimensions_in": "13x10x3",
    "ebay_specifics": {"material": "Cotton"}, "primaryColor": "Blue",
}


@pytest.fixture
def db():
    load_models()
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


@pytest.fixture(autouse=True)
def no_markets(monkeypatch):
    monkeypatch.setattr("vendoo_studio.services.marketplaces.get_selected_marketplaces", lambda: [])


def repair(monkeypatch, listing, operations):
    reply = operations if isinstance(operations, str) else json.dumps(operations)
    call = AsyncMock(return_value=reply)
    monkeypatch.setattr(listing_generate, "collect_provider_text", call)
    result = asyncio.run(listing_generate.fill_validation_gaps(
        object(), listing, analysis="Photo analysis: " + "evidence " * 2000, notes="Seller notes " * 1000))
    return result, call


def test_repair_changes_only_failed_fields_and_keeps_valid_json_payload(monkeypatch):
    original = {**copy.deepcopy(BASE), "price": -1, "sku": "seller-sku", "seller_fields": ["sku"]}
    result, call = repair(monkeypatch, original, [{"op": "replace", "path": "/price", "value": 29}])
    assert result == {**original, "price": 29}
    assert original["price"] == -1
    payload = json.loads(call.await_args.args[1][-1]["content"])
    assert payload["allowed_paths"] == ["/price"]
    assert payload["photo_analysis"].startswith("Photo analysis")


@pytest.mark.parametrize("reply", [
    [{"op": "replace", "path": "/price", "value": 29}, {"op": "replace", "path": "/title", "value": "Unsolicited"}],
    [{"op": "replace", "path": "/price", "value": "not a number"}],
    [{"op": "remove", "path": "/price"}],
    [{"op": "replace", "path": "/seller_fields", "value": []}],
    BASE,
])
def test_repair_rejects_out_of_scope_or_invalid_replies(monkeypatch, reply):
    result, _call = repair(monkeypatch, {**BASE, "price": -1}, reply)
    assert result is None


def test_repair_does_not_touch_seller_owned_invalid_value(monkeypatch):
    result, call = repair(monkeypatch, {**BASE, "price": -1, "seller_fields": ["price"]},
                          [{"op": "replace", "path": "/price", "value": 29}])
    assert result is None
    call.assert_not_awaited()


def test_nested_repair_preserves_sibling_fields(monkeypatch):
    original = {**BASE, "ebay_specifics": {"Pattern": "Invalid", "material": "Cotton"}}
    def blockers(listing):
        return [] if listing["ebay_specifics"]["Pattern"] == "Solid" else [{"field": "ebay_specifics.Pattern", "message": "Invalid option"}]
    monkeypatch.setattr(listing_generate, "_validation_blockers", blockers)
    result, _call = repair(monkeypatch, original, [{"op": "replace", "path": "/ebay_specifics/Pattern", "value": "Solid"}])
    assert result["ebay_specifics"] == {"Pattern": "Solid", "material": "Cotton"}


def test_chat_keeps_old_relevant_seller_evidence_and_saved_clears(db):
    repo = ConversationRepo(db)
    conv = repo.create(title="Item")
    old = repo.add_message(conv.id, "user", "The hem stain is permanent; keep that flaw.")
    for index in range(45):
        repo.add_message(conv.id, "system", f"Background status {index}")
    ListingRepo(db).save_revision(conv.id, {**BASE, "depop_specifics": {"style": []},
                                          "seller_fields": ["depop_specifics.style", "price"]}, source="user_form")
    messages = asyncio.run(chat_prompts.build_chat_messages(conv.id, db, "Describe the hem stain"))
    assert any(message["content"] == old.text for message in messages)
    assert '"depop_specifics.style": []' in messages[0]["content"]
    assert '"price": 29' in messages[0]["content"]
    assert "Saved seller decisions" in messages[0]["content"]


def test_seller_history_budget_preserves_relevant_old_message(db):
    repo = ConversationRepo(db)
    conv = repo.create(title="Item")
    relevant = repo.add_message(conv.id, "user", "Exact inseam is 31 inches; the label says 32.")
    for index in range(30):
        repo.add_message(conv.id, "user", f"Unrelated color discussion {index}. " * 20)
    selected = item_context.seller_messages(repo.get_messages(conv.id), "inseam", max_chars=1500)
    assert relevant.id in {message.id for message in selected}
    assert sum(len(message.text) for message in selected) <= 1500
    other = repo.create(title="Other item")
    repo.add_message(other.id, "user", "Private other item")
    assert "Private other item" not in item_context.seller_answers(repo, conv.id, "inseam")


def test_retrieval_is_independent_of_query_repetition():
    docs = ["cotton tag material", "cotton " * 1000, "shipping label"]
    assert rank_texts("cotton material", docs)[0][0] == 0
    assert rank_texts("cotton material", docs) == rank_texts("cotton " * 50 + "material", docs)


def test_canonical_evidence_policy_is_always_present_and_pinning_is_idempotent():
    rules = relevant_skill_rules(None, "unreadable tag cotton blouse")
    assert "NEVER invent brand names" in rules
    assert "## Input Requirements" in rules
    assert rules.count("## Etsy When Made Estimation") == 1
    assert skill_formulas.with_pinned_formulas(rules) == rules
    assert len(rules) <= 20000


def labelled_outputs(cases):
    from vendoo_studio.services.seller_overrides import set_path
    outputs = {}
    for case in cases:
        listing = copy.deepcopy(BASE)
        for field, expected in case.get("expected_fields", {}).items():
            set_path(listing, field, expected)
        for field in case.get("unsupported_fields", []):
            set_path(listing, field, "")
        if case["id"] == "mens_bottoms":
            listing["title"] = "Fixture 34 Casual Jeans Blue Regular"
        if case["id"] == "unreadable_tags":
            listing["title"] = "Casual Blue T-Shirt Regular Fit"
        if case["id"] == "seller_flaw_measurement":
            listing["description"] = "Casual blue cotton tee with a classic fit.\n\nFlaws:\n- Small hole on left cuff\nSee photos for details.\n\nMeasurements: Pit to pit: 20 inches"
        outputs[case["id"]] = listing
    return outputs


def test_evaluation_corpus_detects_fabrication_ignored_clears_and_missing_cases():
    cases = json.loads((Path(__file__).parent / "fixtures/generation_cases.json").read_text())
    outputs = labelled_outputs(cases)
    report = evaluate_outputs(cases, outputs)
    assert report["passed"], report
    outputs["unreadable_tags"]["brand"] = "Invented Brand"
    outputs["deliberate_clear"]["depop_specifics"]["style"] = ["Vintage"]
    outputs.pop("seller_flaw_measurement")
    report = evaluate_outputs(cases, outputs)
    assert not report["passed"]
    assert report["metrics"]["unsupported_facts_omitted"]["rate"] < 1
    assert report["metrics"]["fact_accuracy"]["rate"] < 1
    assert report["metrics"]["output_present"]["rate"] < 1



def test_timing_preparation_updates_outcome_and_excludes_incomplete_runs(db, monkeypatch):
    from vendoo_studio.services import generation_timing

    monkeypatch.setattr(generation_timing, "SessionLocal", lambda: db)
    timing_id = generation_timing.record_run("synthetic", {"listing_stream": 1}, total_sec=1, outcome="preparing")
    assert generation_timing.recent_summary(db)["finished"] == 0
    generation_timing.add_stages(timing_id, {"native_fields": 1}, total_sec=2, outcome="incomplete")
    assert generation_timing.for_conversation(db, "synthetic")[0]["outcome"] == "incomplete"
    assert generation_timing.recent_summary(db)["finished"] == 0
    generation_timing.add_stages(timing_id, {"finish": 2}, total_sec=3, outcome="done")
    assert generation_timing.recent_summary(db)["finished"] == 1
