#!/usr/bin/env python3
"""Export the shared listing API contract and generate its client models.

The contract is the FastAPI app's own OpenAPI for the operations listed in
``vendoo_studio.routes.api_contract.CONTRACT_OPERATIONS``. This writes:

- ``contracts/anteroom-api.json``: those operations and their schemas
  (OpenAPI 3.1, taken from the hosted app);
- ``vendoo-studio/src/api/contract.gen.ts``: one TypeScript interface per schema;
- ``anteroom-ios/Sources/API/Generated/APIContract.swift``: one Codable struct
  per schema.

Before writing anything it checks that the desktop app (the Mac app's web UI
talks to it) declares the same responses for every shared operation.

    python scripts/api-contract.py           write the three files
    python scripts/api-contract.py --check   fail if any of them is stale

``server/tests/test_api_contract.py`` runs the check in CI.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import tempfile
from pathlib import Path

STUDIO = Path(__file__).resolve().parents[1]
ROOT = STUDIO.parent
SPEC = ROOT / "contracts" / "anteroom-api.json"
TYPESCRIPT = STUDIO / "src" / "api" / "contract.gen.ts"
SWIFT = ROOT / "anteroom-ios" / "Sources" / "API" / "Generated" / "APIContract.swift"
REGENERATE = "python vendoo-studio/scripts/api-contract.py"

# Operations whose request body the desktop app is allowed to declare
# differently: the hosted app is stricter about lengths and unknown keys.
DESKTOP_OWN_BODY = {("post", "/api/conversations"), ("patch", "/api/conversations/{identity}")}


def _openapi(app) -> dict:
    from fastapi.openapi.utils import get_openapi

    # One schema per model, in validation mode: a field with a default is not
    # required, so a client tolerates its absence.
    return get_openapi(title="Anteroom API", version="1", routes=app.routes, separate_input_output_schemas=False)


def _apps():
    os.environ.setdefault("VENDOO_STUDIO_DATA_DIR", tempfile.mkdtemp(prefix="anteroom-contract-"))
    sys.path.insert(0, str(STUDIO / "server"))
    from vendoo_studio.hosted import create_app
    from vendoo_studio.main import app as desktop

    return create_app("https://contract.supabase.co"), desktop


def _template(path: str) -> str:
    """A path with its parameter names erased, to match the two apps' spellings."""
    return re.sub(r"\{[^}]+\}", "{}", path)


def _refs(value, found: set[str]) -> None:
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            found.add(ref.rsplit("/", 1)[1])
        for item in value.values():
            _refs(item, found)
    elif isinstance(value, list):
        for item in value:
            _refs(item, found)


def _operation(spec: dict, method: str, path: str) -> dict:
    for candidate, operations in spec["paths"].items():
        if _template(candidate) == _template(path) and method in operations:
            return operations[method]
    raise SystemExit(f"{method.upper()} {path} is in CONTRACT_OPERATIONS but the app does not serve it.")


def _untitled(operation: dict) -> dict:
    """The operation without the titles FastAPI makes up from Python function names."""
    operation = copy.deepcopy(operation)
    for part in [operation.get("requestBody") or {}, *operation.get("responses", {}).values()]:
        for media in part.get("content", {}).values():
            media.get("schema", {}).pop("title", None)
    return operation


def _shared(operation: dict) -> dict:
    """The parts of an operation both apps must agree on."""
    return {"responses": operation.get("responses", {}), "requestBody": operation.get("requestBody")}


def _schemas_used(spec: dict, operations: dict) -> dict:
    """Every component schema the operations reach, directly or through another schema."""
    schemas = spec["components"]["schemas"]
    pending, found = set(), set()
    _refs(operations, pending)
    while pending:
        name = pending.pop()
        if name not in found:
            found.add(name)
            _refs(schemas[name], pending)
    return {name: schemas[name] for name in sorted(found)}


def contract(hosted, desktop) -> dict:
    from vendoo_studio.routes.api_contract import CONTRACT_OPERATIONS, HOSTED_ONLY_OPERATIONS

    hosted_spec, desktop_spec = _openapi(hosted), _openapi(desktop)
    paths: dict[str, dict] = {}
    drift = []
    for method, path in CONTRACT_OPERATIONS:
        operation = _untitled(_operation(hosted_spec, method, path))
        operation.pop("operationId", None)
        operation.pop("tags", None)
        paths.setdefault(path, {})[method] = operation
        if (method, path) in HOSTED_ONLY_OPERATIONS:
            continue
        theirs = _shared(_untitled(_operation(desktop_spec, method, path)))
        ours = _shared(operation)
        if (method, path) in DESKTOP_OWN_BODY:
            theirs.pop("requestBody")
            ours.pop("requestBody")
        # Both apps must name the same schemas, and those schemas must match.
        mismatch = theirs != ours
        mine, other = set(), set()
        _refs(ours, mine)
        _refs(theirs, other)
        mismatch |= any(hosted_spec["components"]["schemas"].get(name) != desktop_spec["components"]["schemas"].get(name)
                        for name in mine | other)
        if mismatch:
            drift.append(f"{method.upper()} {path}")
    if drift:
        raise SystemExit("The desktop and hosted apps declare different contracts for: " + ", ".join(drift)
                         + ". Both must use the models in vendoo_studio/routes/api_contract.py.")
    return {
        "openapi": hosted_spec["openapi"],
        "info": {"title": "Anteroom listing API contract", "version": "1",
                 "description": "Generated from vendoo_studio/routes/api_contract.py by " + REGENERATE + ". Do not edit."},
        "paths": paths,
        "components": {"schemas": _schemas_used(hosted_spec, paths)},
    }


# --- Types shared by both generators -----------------------------------------------------------


def _nullable(schema: dict) -> tuple[dict, bool]:
    options = schema.get("anyOf")
    if options and any(option.get("type") == "null" for option in options):
        rest = [option for option in options if option.get("type") != "null"]
        if len(rest) == 1:
            return rest[0], True
    return schema, False


def _doc(text: str | None, indent: str, style: str) -> list[str]:
    if not text:
        return []
    lines = [line.rstrip() for line in text.strip().splitlines()]
    if style == "swift":
        return [f"{indent}/// {line}".rstrip() for line in lines]
    if len(lines) == 1:
        return [f"{indent}/** {lines[0]} */"]
    return [f"{indent}/**", *[f"{indent} * {line}".rstrip() for line in lines], f"{indent} */"]


def _header(comment: str) -> list[str]:
    return [
        f"{comment} Generated by {REGENERATE} from contracts/anteroom-api.json. Do not edit.",
        f"{comment} The models live in vendoo-studio/server/vendoo_studio/routes/api_contract.py.",
    ]


# --- TypeScript ---------------------------------------------------------------------------------


def _ts_type(schema: dict) -> str:
    schema, nullable = _nullable(schema)
    text = _ts_value(schema)
    return f"{text} | null" if nullable else text


def _ts_value(schema: dict) -> str:
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[1]
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return " | ".join(json.dumps(value) for value in schema["enum"])
    kind = schema.get("type")
    if kind == "string":
        return "string"
    if kind in ("integer", "number"):
        return "number"
    if kind == "boolean":
        return "boolean"
    if kind == "array":
        item = _ts_type(schema.get("items", {}))
        return f"({item})[]" if "|" in item else f"{item}[]"
    if kind == "object":
        extra = schema.get("additionalProperties", True)
        return "Record<string, unknown>" if extra is True else f"Record<string, {_ts_type(extra)}>"
    if "anyOf" in schema:
        return " | ".join(_ts_type(option) for option in schema["anyOf"])
    return "unknown"


def typescript(spec: dict) -> str:
    lines = _header("//")
    for name, schema in spec["components"]["schemas"].items():
        lines.append("")
        lines += _doc(schema.get("description"), "", "ts")
        lines.append(f"export interface {name} {{")
        required = set(schema.get("required", []))
        for field, value in schema.get("properties", {}).items():
            lines += _doc(value.get("description"), "  ", "ts")
            optional = "" if field in required else "?"
            lines.append(f"  {field}{optional}: {_ts_type(value)};")
        lines.append("}")
    return "\n".join(lines) + "\n"


# --- Swift --------------------------------------------------------------------------------------

ACRONYMS = {"id": "ID", "ids": "IDs", "url": "URL", "urls": "URLs"}


def swift_name(field: str) -> str:
    first, *rest = field.split("_")
    return first + "".join(ACRONYMS.get(part, part[:1].upper() + part[1:]) for part in rest)


def _swift_type(schema: dict) -> tuple[str, bool]:
    schema, nullable = _nullable(schema)
    return _swift_value(schema), nullable


def _swift_value(schema: dict) -> str:
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[1]
    # Literals decode as their base type, so a value a newer server adds
    # never fails to decode on an older phone.
    kind = schema.get("type")
    if kind is None and ("const" in schema or "enum" in schema):
        values = [schema["const"]] if "const" in schema else schema["enum"]
        kind = "integer" if all(isinstance(value, int) for value in values) else "string"
    if kind == "string":
        return "String"
    if kind == "integer":
        return "Int"
    if kind == "number":
        return "Double"
    if kind == "boolean":
        return "Bool"
    if kind == "array":
        item, nullable = _swift_type(schema.get("items", {}))
        return f"[{item}{'?' if nullable else ''}]"
    if kind == "object":
        extra = schema.get("additionalProperties", True)
        if extra is True:
            return "[String: JSONValue]"
        value, nullable = _swift_type(extra)
        return f"[String: {value}{'?' if nullable else ''}]"
    return "JSONValue"


def _swift_literal(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[]" if not value else "[" + ", ".join(_swift_literal(item) for item in value) + "]"
    if isinstance(value, dict):
        if value:
            raise SystemExit("Swift generation supports only empty dictionary defaults.")
        return "[:]"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return json.dumps(value)


def _allowed(schema: dict) -> str | None:
    schema, _ = _nullable(schema)
    if schema.get("type") == "array":
        schema = schema.get("items", {})
    values = [schema["const"]] if "const" in schema else schema.get("enum")
    return "One of: " + ", ".join(str(value) for value in values) + "." if values else None


def swift(spec: dict) -> str:
    lines = [*_header("//"), "", "import Foundation"]
    for name, schema in spec["components"]["schemas"].items():
        required = set(schema.get("required", []))
        properties = schema.get("properties", {})
        fields = []
        for field, value in properties.items():
            kind, nullable = _swift_type(value)
            default = value.get("default")
            fallback = field not in required and default is not None
            fields.append((field, swift_name(field), kind, nullable or (field not in required and not fallback),
                           _swift_literal(default) if fallback else None, value))
        conformances = ["Codable", "Hashable", "Sendable"]
        if any(swift == "id" and kind == "String" and not optional for _, swift, kind, optional, _, _ in fields):
            conformances.insert(0, "Identifiable")
        lines.append("")
        lines += _doc(schema.get("description"), "", "swift")
        lines.append(f"struct {name}: {', '.join(conformances)} {{")
        for field, swift_field, kind, optional, default, value in fields:
            lines += _doc(value.get("description") or _allowed(value), "    ", "swift")
            suffix = "?" if optional else (f" = {default}" if default is not None else "")
            lines.append(f"    var {swift_field}: {kind}{suffix}")
        renamed = [(field, swift_field) for field, swift_field, *_ in fields if field != swift_field]
        if renamed:
            lines += ["", "    enum CodingKeys: String, CodingKey {"]
            plain = [swift_field for field, swift_field, *_ in fields if field == swift_field]
            if plain:
                lines.append(f"        case {', '.join(plain)}")
            lines += [f'        case {swift_field} = "{field}"' for field, swift_field in renamed]
            lines.append("    }")
        lines.append("}")
        if any(default is not None for *_, default, _ in fields):
            # A field with a default may be missing; it decodes to that default.
            lines += ["", f"extension {name} {{", "    init(from decoder: Decoder) throws {",
                      "        let values = try decoder.container(keyedBy: CodingKeys.self)"]
            for _field, swift_field, kind, optional, default, _value in fields:
                if default is not None:
                    lines.append(f"        {swift_field} = try values.decodeIfPresent({kind}.self, forKey: .{swift_field}) ?? {default}")
                elif optional:
                    lines.append(f"        {swift_field} = try values.decodeIfPresent({kind}.self, forKey: .{swift_field})")
                else:
                    lines.append(f"        {swift_field} = try values.decode({kind}.self, forKey: .{swift_field})")
            lines += ["    }", "}"]
    return "\n".join(lines) + "\n"


def outputs() -> dict[Path, str]:
    spec = contract(*_apps())
    return {
        SPEC: json.dumps(spec, indent=2, sort_keys=True) + "\n",
        TYPESCRIPT: typescript(spec),
        SWIFT: swift(spec),
    }


def stale(files: dict[Path, str]) -> list[Path]:
    return [path for path, text in files.items() if not path.exists() or path.read_text() != text]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--check", action="store_true", help="fail if a generated file is out of date")
    args = parser.parse_args()
    files = outputs()
    if args.check:
        old = stale(files)
        for path in old:
            print(f"{path.relative_to(ROOT)} is out of date. Run: {REGENERATE}", file=sys.stderr)
        return 1 if old else 0
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
