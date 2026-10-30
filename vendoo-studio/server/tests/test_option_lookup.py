from __future__ import annotations

import asyncio
import copy
import json

import pytest

from vendoo_studio.services import option_lookup as lookup
from vendoo_studio.services.native_field_fill import validated_values


def forms():
    labels = [f"Catalog brand {i}" for i in range(5000)] + ["Chase Authentics", "Fruit of the Loom"]
    return {"ebay": {"fields": [{"path": "ebay_specifics.brand", "options": labels,
                                "selection_only": True,
                                "option_values": {label: str(i) for i, label in enumerate(labels)}}]}}


def search(query="", offset=0, field="ebay_specifics.brand", marketplace="ebay"):
    return dict(marketplace=marketplace, field=field, query=query, offset=offset)


def test_searches_complete_catalog_fuzzily_and_returns_exact_ids():
    source = forms()
    before = copy.deepcopy(source)
    catalog = lookup.OptionCatalog(source)
    result = catalog.search(search("chase autentics"))
    assert result["results"][0] == {"label": "Chase Authentics", "value": "5000"}
    assert catalog.search(search("Fruit of the Loom"))["results"][0]["value"] == "5001"
    assert source == before
    # A snapshot's pages cannot change underneath a request.
    source["ebay"]["fields"][0]["options"].clear()
    assert catalog.search(search("Chase Authentics"))["results"][0]["value"] == "5000"


def test_can_browse_every_option_with_stable_pages():
    catalog = lookup.OptionCatalog(forms())
    offset, labels = 0, []
    while offset is not None:
        result = catalog.search(search(offset=offset))
        assert len(result["results"]) <= lookup.PAGE_SIZE
        labels += [item["label"] for item in result["results"]]
        offset = result["next_offset"]
    assert labels == forms()["ebay"]["fields"][0]["options"]


@pytest.mark.parametrize("args", [None, [], search(offset=-1), search(offset=True), search(query=3),
                                  search(query="x" * 201), search(field="secret"), search(marketplace="other")])
def test_rejects_invalid_requests_and_unknown_scopes(args):
    assert "error" in lookup.OptionCatalog(forms()).search(args)


def test_no_match_is_empty_and_constraints_are_preserved():
    source = forms()
    row = source["ebay"]["fields"][0]
    row["value_constraints"] = {"Chase Authentics": [{"parentName": "Sport", "parentValue": "Racing"}]}
    catalog = lookup.OptionCatalog(source)
    result = catalog.search(search("Chase Authentics"))
    assert result["results"][0]["constraints"] == row["value_constraints"]["Chase Authentics"]
    assert catalog.search(search("zzzzzzzzzzzzzzzzzzzz"))["results"] == []


class Provider:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.messages = []

    async def chat(self, messages, stream=True):
        self.messages.append(copy.deepcopy(messages))
        text = next(self.replies)
        # Split JSON across chunks like a real streaming provider.
        yield text[:11]
        yield text[11:]


def run(provider, catalog):
    async def collect():
        return [chunk async for chunk in lookup.chat_with_options(provider, [{"role": "user", "content": "Write a listing"}], catalog)]
    return asyncio.run(collect())


def test_agent_reformulates_then_uses_verified_choice_without_leaking_tool_json():
    first = json.dumps({"_option_search": [search("zzzzzzzzzzzzzzzzzzzz")]})
    second = json.dumps({"_option_search": [search("chase autentics")]})
    final = '{"fields":{"ebay_specifics.brand":"5000"}}'
    provider = Provider([first, "```json\n" + second + "\n```", final])
    source = forms()
    assert run(provider, lookup.OptionCatalog(source)) == [final]
    assert '"results": []' in provider.messages[1][-1]["content"]
    assert '"label": "Chase Authentics", "value": "5000"' in provider.messages[2][-1]["content"]
    rows = {row["path"]: row for row in source["ebay"]["fields"]}
    assert validated_values(json.loads(final), rows)["ebay_specifics.brand"][0] == "5000"
    assert not validated_values({"fields": {"ebay_specifics.brand": "invented"}}, rows)


def test_agent_can_request_a_later_page():
    provider = Provider([json.dumps({"_option_search": [search(offset=20)]}), "done"])
    assert run(provider, lookup.OptionCatalog(forms())) == ["done"]
    result = json.loads(provider.messages[1][-1]["content"].split("\n", 1)[1])[0]
    assert result["results"][0]["label"] == "Catalog brand 20"
    assert result["next_offset"] == 40


def test_protocol_errors_are_returned_to_agent_for_correction():
    provider = Provider(['{"_option_search": []}', json.dumps({"_option_search": [search()]}), "done"])
    assert run(provider, lookup.OptionCatalog(forms())) == ["done"]
    assert "one to four" in provider.messages[1][-1]["content"]


def test_lookup_loop_is_bounded(monkeypatch):
    monkeypatch.setattr(lookup, "MAX_ROUNDS", 2)
    provider = Provider([json.dumps({"_option_search": [search()]})] * 4)
    with pytest.raises(RuntimeError, match="limit reached"):
        run(provider, lookup.OptionCatalog(forms()))
    assert len(provider.messages) == 3


def test_small_dropdowns_stream_without_an_extra_call_or_tool_instructions():
    source = {"ebay": {"fields": [{"path": "color", "options": ["Red", "Blue"]}]}}
    provider = Provider(["A red shirt"])
    assert "".join(run(provider, lookup.OptionCatalog(source))) == "A red shirt"
    assert len(provider.messages[0]) == 1
    assert lookup.project_row(source["ebay"]["fields"][0]) == source["ebay"]["fields"][0]


def test_oversized_choice_metadata_does_not_overflow_context():
    source = forms()
    source["ebay"]["fields"][0]["value_constraints"] = {"Chase Authentics": [{"parentValue": "x" * 20000}]}
    result = lookup.OptionCatalog(source).search(search("Chase Authentics"))
    assert "error" in result
    assert len(json.dumps(result)) < 200


def test_declares_field_targets_before_editing_and_keeps_protocol_out_of_answer():
    catalog = lookup.OptionCatalog({'ebay': {'fields': [
        {'path': 'ebay_specifics.Size', 'label': 'Size'},
        {'path': 'ebay_specifics.Brand', 'label': 'Brand'},
    ]}})
    provider = Provider(['{"_field_targets":["ebay_specifics.Size"]}', 'Saved size 36.'])
    targets = []

    async def collect():
        return [chunk async for chunk in lookup.chat_with_options(
            provider, [{'role': 'user', 'content': 'Change the eBay size to 36'}], catalog,
            on_targets=targets.append)]

    assert asyncio.run(collect()) == ['Saved size 36.']
    assert targets == [('ebay_specifics.Size',)]
    assert 'Field targets acknowledged' in provider.messages[1][-1]['content']


def test_unknown_and_readonly_field_targets_are_rejected_and_empty_targets_are_allowed():
    catalog = lookup.OptionCatalog({'ebay': {'fields': [
        {'path': 'ebay_specifics.Size'}, {'path': 'ebay_specifics.shipFrom', 'locked': True},
    ]}})
    provider = Provider(['{"_field_targets":["unknown"]}',
                         '{"_field_targets":["ebay_specifics.shipFrom"]}',
                         '{"_field_targets":[]}', 'Which size field do you mean?'])
    targets = []

    async def collect():
        return [chunk async for chunk in lookup.chat_with_options(
            provider, [], catalog, on_targets=targets.append)]

    assert asyncio.run(collect()) == ['Which size field do you mean?']
    assert targets == [()]
    assert 'Invalid field targets' in provider.messages[1][-1]['content']
    assert 'Invalid field targets' in provider.messages[2][-1]['content']
