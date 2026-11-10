"""scripts/api-contract.py: the shared API contract and its generated client models stay current."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "api-contract.py"
spec = importlib.util.spec_from_file_location("api_contract_script", SCRIPT)
assert spec and spec.loader
api_contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api_contract)


def test_generated_contract_files_are_current():
    """A backend change that alters the contract must regenerate the spec, the
    TypeScript types and the Swift models in the same change. This also fails
    when the desktop routes declare a different contract from the hosted ones."""
    stale = api_contract.stale(api_contract.outputs())
    assert not stale, f"Out of date: {[str(path) for path in stale]}. Run: {api_contract.REGENERATE}"


def test_desktop_routes_that_drift_from_the_hosted_contract_fail():
    from fastapi import FastAPI

    from vendoo_studio.routes.api_contract import ConversationSummary

    hosted, desktop = api_contract._apps()
    drifted = FastAPI()
    for route in desktop.routes:
        if (getattr(route, "path", ""), getattr(route, "methods", None)) != ("/api/conversations", {"GET"}):
            drifted.router.routes.append(route)

    class Wider(ConversationSummary):
        extra_field: str = ""

    @drifted.get("/api/conversations", response_model=list[Wider])
    def listings():
        return []

    with pytest.raises(SystemExit, match="GET /api/conversations"):
        api_contract.contract(hosted, drifted)


SCHEMA = {
    "openapi": "3.1.0",
    "paths": {},
    "components": {"schemas": {
        "Row": {
            "type": "object",
            "description": "A row.",
            "required": ["id", "title", "box_url"],
            "properties": {
                "id": {"type": "string"},
                "title": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "box_url": {"type": "string", "description": "Where the box is."},
                "seen_ids": {"type": "array", "items": {"type": "string"}, "default": []},
                "kind": {"enum": ["a", "b"], "type": "string", "default": "a"},
                "turn": {"enum": [0, 90], "type": "integer", "default": 0},
                "data": {"type": "object", "additionalProperties": True},
                "price": {"anyOf": [{"type": "number"}, {"type": "null"}], "default": None},
            },
        },
    }},
}


def test_typescript_marks_defaults_optional_and_keeps_literal_unions():
    text = api_contract.typescript(SCHEMA)
    assert "/** A row. */\nexport interface Row {" in text
    assert "  id: string;\n  title: string | null;\n" in text
    assert "  /** Where the box is. */\n  box_url: string;\n" in text
    assert '  seen_ids?: string[];\n  kind?: "a" | "b";\n  turn?: 0 | 90;\n' in text
    assert "  data?: Record<string, unknown>;\n  price?: number | null;\n" in text


def test_swift_names_fields_like_the_app_and_decodes_missing_defaults():
    text = api_contract.swift(SCHEMA)
    assert "struct Row: Identifiable, Codable, Hashable, Sendable {" in text
    # Literals decode as their base type, so a value a newer server adds still decodes.
    assert "    var kind: String = \"a\"\n" in text
    assert "    var turn: Int = 0\n" in text
    assert "    var boxURL: String\n" in text and "    var seenIDs: [String] = []\n" in text
    assert "    var data: [String: JSONValue]?\n    var price: Double?\n" in text
    assert '        case boxURL = "box_url"\n' in text
    assert "seenIDs = try values.decodeIfPresent([String].self, forKey: .seenIDs) ?? []" in text
    assert "boxURL = try values.decode(String.self, forKey: .boxURL)" in text
