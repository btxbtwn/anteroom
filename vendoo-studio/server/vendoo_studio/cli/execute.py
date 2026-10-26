"""Turn a catalog command, or an ad-hoc request, into one HTTP call."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from vendoo_studio.cli.catalog import is_destructive, match_command
from vendoo_studio.cli.client import AnteroomClient
from vendoo_studio.cli.constants import DEFAULT_STREAM_SECONDS
from vendoo_studio.cli.errors import ConfirmRequired, UsageError


def run_command(command: dict[str, Any], args: argparse.Namespace, client: AnteroomClient) -> dict[str, Any]:
    if not command.get("supported", True):
        raise UsageError(command.get("unsupported_reason") or f"{command['method']} {command['path']} is not callable from the CLI")
    path = _fill_path(command, args)
    query = _collect_query(command, args)
    content_type = command.get("body_content_type")
    if content_type == "multipart/form-data":
        form, files = _collect_form(command, args)
        json_body = None
        if getattr(args, "body", None):
            raise UsageError("This command uploads files. Pass the file flags, not --body.")
    else:
        form, files = {}, []
        json_body = _collect_json(command, args)
    _guard(command, json_body if json_body is not None else form, confirmed=bool(args.confirm))
    if args.confirm and _has_confirm(command):
        if not isinstance(json_body, dict):
            json_body = {} if json_body is None else json_body
        if isinstance(json_body, dict):
            json_body["confirm"] = True
    stream_seconds = args.stream_seconds if command.get("stream") else None
    return client.call(
        command["method"],
        path,
        query=query,
        json_body=json_body,
        form=form or None,
        files=files,
        headers=_headers(args),
        stream_seconds=stream_seconds,
        output=args.output,
    )


def run_raw(method: str, path: str, args: argparse.Namespace, client: AnteroomClient, catalog: dict[str, Any]) -> dict[str, Any]:
    method = method.upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise UsageError("Method must be GET, POST, PUT, PATCH, or DELETE")
    if not path.startswith("/"):
        raise UsageError("Path must start with /")
    body = _parse_body_text(args.body) if args.body else None
    matched = match_command(catalog, method, path.split("?", 1)[0]) if catalog else None
    if matched and matched.get("destructive") and not args.confirm:
        raise ConfirmRequired(matched.get("destructive_reason") or "this route is destructive")
    names = set()
    if matched:
        names = {item["name"] for item in matched.get("parameters") or [] if item.get("in") in {"body", "form"}}
    reason = None if matched else is_destructive(method, path.split("?", 1)[0], names)
    if reason and not args.confirm:
        raise ConfirmRequired(reason)
    if isinstance(body, dict) and body.get("confirm") is True and not args.confirm:
        raise ConfirmRequired("the body sets confirm to true")
    if args.confirm and matched and "confirm" in names:
        if body is None:
            body = {}
        if isinstance(body, dict):
            body["confirm"] = True
    query = _parse_query_flags(args.query)
    # A query string on the path is kept by the server; flags add more via params.
    # Split them so httpx does not drop the path query.
    bare, embedded = _split_query(path)
    embedded.update(query)
    stream_seconds = args.stream_seconds
    if stream_seconds is None and matched and matched.get("stream"):
        stream_seconds = DEFAULT_STREAM_SECONDS
    return client.call(
        method,
        bare,
        query=embedded,
        json_body=body,
        headers=_headers(args),
        stream_seconds=stream_seconds,
        output=args.output,
    )


def _guard(command: dict[str, Any], body: Any, *, confirmed: bool) -> None:
    if command.get("destructive") and not confirmed:
        raise ConfirmRequired(command.get("destructive_reason") or "this command is destructive")
    if isinstance(body, dict) and body.get("confirm") is True and not confirmed:
        raise ConfirmRequired("the body sets confirm to true")


def _has_confirm(command: dict[str, Any]) -> bool:
    return any(item["name"] == "confirm" and item["in"] == "body" for item in command.get("parameters") or [])


def _fill_path(command: dict[str, Any], args: argparse.Namespace) -> str:
    path = command["path"]
    for parameter in command.get("parameters") or []:
        if parameter["in"] != "path":
            continue
        value = getattr(args, dest_name("path", parameter["name"]))
        if value is None or value == "":
            raise UsageError(f"Missing --{flag_name(parameter['name'])}")
        path = path.replace("{" + parameter["name"] + "}", _quote(str(value)))
        # Starlette also accepts {name:converter}.
        needle = "{" + parameter["name"] + ":"
        if needle in path:
            start = path.index(needle)
            end = path.index("}", start)
            path = path[:start] + _quote(str(value)) + path[end + 1:]
    return path


def _collect_query(command: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    query: dict[str, Any] = {}
    for parameter in command.get("parameters") or []:
        if parameter["in"] != "query":
            continue
        value = getattr(args, dest_name("query", parameter["name"]), None)
        if value is None:
            continue
        query[parameter["name"]] = value
    return query


def _collect_json(command: dict[str, Any], args: argparse.Namespace) -> Any:
    fields = [item for item in command.get("parameters") or [] if item["in"] == "body"]
    if not fields and not args.body:
        return None
    if len(fields) == 1 and fields[0]["name"] == "body" and fields[0]["type"] != "object":
        if args.body:
            parsed = _parse_body_text(args.body)
            return parsed
        value = getattr(args, dest_name("body", "body"), None)
        if value is None:
            return None
        return _coerce(fields[0], value)
    body = _parse_body_text(args.body) if args.body else {}
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise UsageError("--body must be a JSON object so it can be merged with flags")
    for parameter in fields:
        if parameter["name"] == "confirm":
            continue
        value = getattr(args, dest_name("body", parameter["name"]), None)
        if value is None:
            continue
        body[parameter["name"]] = _coerce(parameter, value)
    if body:
        return body
    # A model whose fields all have defaults still requires a JSON object.
    # Dropping {} makes the server answer 422 (gender check with no --apply).
    return {} if fields else None


def _collect_form(command: dict[str, Any], args: argparse.Namespace) -> tuple[dict[str, Any], list[tuple[str, Path]]]:
    form: dict[str, Any] = {}
    files: list[tuple[str, Path]] = []
    for parameter in command.get("parameters") or []:
        if parameter["in"] == "form":
            value = getattr(args, dest_name("form", parameter["name"]), None)
            if value is None:
                if parameter.get("required"):
                    raise UsageError(f"Missing --{flag_name(parameter['name'])}")
                continue
            form[parameter["name"]] = _coerce(parameter, value)
        elif parameter["in"] == "file":
            value = getattr(args, dest_name("file", parameter["name"]), None)
            if not value:
                if parameter.get("required"):
                    raise UsageError(f"Missing --{flag_name(parameter['name'])}")
                continue
            paths = value if isinstance(value, list) else [value]
            for raw in paths:
                path = Path(raw).expanduser()
                if not path.is_file():
                    raise UsageError(f"File not found: {path}")
                files.append((parameter["name"], path))
    return form, files


def _coerce(parameter: dict[str, Any], value: Any) -> Any:
    if parameter.get("type") == "array" and isinstance(value, list):
        items = parameter.get("items") or "string"
        converted = []
        for item in value:
            if isinstance(item, str) and item.strip().startswith("["):
                parsed = json.loads(item)
                if not isinstance(parsed, list):
                    raise UsageError(f"--{flag_name(parameter['name'])} JSON value must be an array")
                converted.extend(parsed)
            else:
                converted.append(_coerce_scalar(items, item))
        return converted
    if parameter.get("type") in {"object", "json"} and isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise UsageError(f"--{flag_name(parameter['name'])} must be JSON ({exc})") from exc
    return value


def _coerce_scalar(kind: str, value: Any) -> Any:
    if kind == "integer" and isinstance(value, str):
        return int(value)
    if kind == "number" and isinstance(value, str):
        return float(value)
    if kind == "boolean" and isinstance(value, str):
        return parse_bool(value)
    if kind in {"object", "json"} and isinstance(value, str):
        return json.loads(value)
    return value


def _parse_body_text(text: str | None) -> Any:
    if text is None:
        return None
    if text == "-":
        import sys
        text = sys.stdin.read()
    elif text.startswith("@"):
        path = Path(text[1:]).expanduser()
        if not path.is_file():
            raise UsageError(f"Body file not found: {path}")
        text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise UsageError(f"--body is not valid JSON ({exc})") from exc


def _parse_query_flags(items: list[str] | None) -> dict[str, Any]:
    query: dict[str, list[str]] = {}
    for item in items or []:
        if "=" not in item:
            raise UsageError("--query must be key=value")
        key, value = item.split("=", 1)
        if not key:
            raise UsageError("--query must be key=value")
        query.setdefault(key, []).append(value)
    return {key: value if len(value) > 1 else value[0] for key, value in query.items()}


def _split_query(path: str) -> tuple[str, dict[str, str]]:
    if "?" not in path:
        return path, {}
    bare, query = path.split("?", 1)
    parsed: dict[str, str] = {}
    for piece in query.split("&"):
        if not piece:
            continue
        if "=" not in piece:
            parsed[piece] = ""
        else:
            key, value = piece.split("=", 1)
            parsed[key] = value
    return bare, parsed


def _headers(args: argparse.Namespace) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in args.header or []:
        if ":" not in item:
            raise UsageError("--header must be 'Name: value'")
        name, value = item.split(":", 1)
        if not name.strip():
            raise UsageError("--header must be 'Name: value'")
        headers[name.strip()] = value.strip()
    return headers


def _quote(value: str) -> str:
    from urllib.parse import quote
    return quote(value, safe="")


def dest_name(prefix: str, name: str) -> str:
    cleaned = "".join(char if char.isalnum() else "_" for char in name)
    return f"{prefix}_{cleaned}"


def flag_name(name: str) -> str:
    return name.replace("_", "-")


def arguments_to_namespace(command: dict[str, Any], arguments: dict[str, Any]) -> argparse.Namespace:
    """Build the same namespace the CLI parser would, from an MCP tool call."""
    values: dict[str, Any] = {
        "confirm": arguments.get("confirm") is True,
        "body": None,
        "header": None,
        "output": arguments.get("output"),
        "stream_seconds": arguments.get("stream_seconds"),
        "query": None,
    }
    body = arguments.get("body")
    if isinstance(body, dict):
        values["body"] = json.dumps(body)
    elif isinstance(body, str):
        values["body"] = body
    for parameter in command.get("parameters") or []:
        if parameter["name"] == "confirm" and parameter["in"] == "body":
            continue
        prefix = {"path": "path", "query": "query", "body": "body", "form": "form", "file": "file"}[parameter["in"]]
        values[dest_name(prefix, parameter["name"])] = arguments.get(parameter["name"])
    return argparse.Namespace(**values)


def parse_bool(text: str) -> bool:
    lowered = text.lower()
    if lowered in {"1", "true", "yes"}:
        return True
    if lowered in {"0", "false", "no"}:
        return False
    raise UsageError(f"Expected true or false, got {text!r}")
