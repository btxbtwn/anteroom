"""Command catalog generated from Anteroom's FastAPI routes.

The running CLI caches the result. A route-module change invalidates the
cache, so new endpoints show up as commands without a hand-written list.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
from pathlib import Path
from typing import Any

from vendoo_studio.cli.constants import AUTH_NOTE, DEFAULT_BASE_URL, RESERVED_GROUPS
from vendoo_studio.cli.errors import UsageError

# Path segments that delete, publish, or otherwise change something the seller
# cannot trivially undo. ``list`` is not here: most ``list`` routes only read.
DESTRUCTIVE_SEGMENTS = frozenset({
    "delete",
    "delist",
    "cancel",
    "reset",
    "prune",
    "restart",
    "reinstall",
    "unlink",
    "pause",
    "dismiss",
    "forget",
    "logout",
    "remove",
})

_VERBS = {
    "GET": "get",
    "POST": "create",
    "PUT": "replace",
    "PATCH": "update",
    "DELETE": "delete",
    "WEBSOCKET": "ws",
}

_SKIP_METHODS = frozenset({"HEAD", "OPTIONS"})
_DOCS_PATHS = frozenset({"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})


def package_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def sources_fingerprint() -> str:
    """Hash the server package so a model or route edit rebuilds the catalog."""
    root = package_dir()
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        if "migrations" in path.parts or "__pycache__" in path.parts:
            continue
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:20]


def cache_file() -> Path:
    override = os.environ.get("ANTEROOM_CACHE_DIR")
    base = Path(override).expanduser() if override else Path.home() / ".cache" / "anteroom"
    return base / f"catalog-{sources_fingerprint()}.json"


def load_catalog() -> dict[str, Any]:
    """Return the command catalog, rebuilding it when the server sources change."""
    override = os.environ.get("ANTEROOM_CATALOG_FILE")
    if override:
        return json.loads(Path(override).read_text(encoding="utf-8"))
    path = cache_file()
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    catalog = build_catalog_from_app()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return catalog


def build_catalog_from_app() -> dict[str, Any]:
    """Import the FastAPI app and describe every route. Does not start the server."""
    from fastapi.routing import APIRoute, APIWebSocketRoute, iter_route_contexts
    from starlette.routing import Mount, Route, WebSocketRoute

    from vendoo_studio.main import app

    schema = app.openapi()
    commands: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    # FastAPI keeps included routers lazy. iter_route_contexts applies each
    # router's prefix so the path here is the path a client calls.
    for context in iter_route_contexts(app.routes):
        route = context.original_route
        path = context.path_format or context.path or getattr(route, "path", None)
        if not isinstance(path, str):
            continue
        if isinstance(route, APIRoute):
            for method in sorted((context.methods or set()) - _SKIP_METHODS):
                key = (method, path)
                if key in seen:
                    continue
                seen.add(key)
                operation = (schema.get("paths") or {}).get(path, {}).get(method.lower(), {})
                commands.append(_from_http(context, method, operation, schema, path))
        elif isinstance(route, (APIWebSocketRoute, WebSocketRoute)):
            key = ("WEBSOCKET", path)
            if key in seen:
                continue
            seen.add(key)
            commands.append(_from_websocket(context, path))
        elif isinstance(route, Mount):
            skipped.append({
                "path": path or "/",
                "type": "mount",
                "reason": (
                    "Static frontend. The mounted files are the web app, not an API. "
                    "Use the HTTP routes under /api, or anteroom api request for a path the mount serves."
                ),
            })
        elif isinstance(route, Route):
            methods = sorted(set(context.methods or route.methods or []) - _SKIP_METHODS)
            for method in methods:
                key = (method, path)
                if key in seen:
                    continue
                seen.add(key)
                commands.append(_from_plain(context, method, path))

    assign_actions(commands)
    commands.sort(key=lambda item: (item["group"], item["action"], item["method"], item["path"]))
    return {
        "cli": "anteroom",
        "default_base_url": DEFAULT_BASE_URL,
        "auth": AUTH_NOTE,
        "commands": commands,
        "skipped": skipped,
        "not_wrapped": [
            {
                "service": "webhook-relay",
                "reason": (
                    "Cloudflare worker that receives eBay and Etsy webhooks and brokers sign-in. "
                    "Anteroom calls it outbound. It is not part of the local HTTP API, and its device "
                    "token can unlink accounts, so the CLI does not call it. Marketplace actions go "
                    "through the ebay, etsy, and marketplace-accounts commands."
                ),
            },
            {
                "service": "vendoo-extension",
                "reason": (
                    "The Chrome extension has no HTTP API. It pairs with Anteroom over the extension "
                    "WebSocket, which this CLI lists and does not open. Use the extension commands for "
                    "pairing, status, and reload."
                ),
            },
        ],
    }


def assign_actions(commands: list[dict[str, Any]]) -> None:
    """Fill ``group`` (already set) and ``action`` so every command name is unique."""
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for command in commands:
        base, params = _action_base(command)
        command["_base"] = base
        command["_params"] = params
        buckets.setdefault((command["group"], base), []).append(command)

    for items in buckets.values():
        if len(items) == 1:
            items[0]["action"] = items[0]["_base"]
            continue
        methods = {item["method"] for item in items}
        if len(methods) == len(items):
            for item in items:
                if item["_base"] in _VERBS.values() and not _static_tokens(item):
                    item["action"] = item["_base"]
                else:
                    item["action"] = f"{item['_base']}-{_VERBS.get(item['method'], item['method'].lower())}"
            continue
        for item in items:
            if item["_params"]:
                item["action"] = f"{item['_base']}-{'-'.join(item['_params'])}"
            else:
                item["action"] = item["_base"]

    for command in commands:
        command["action"] = _clean_action(command.get("action") or "")
    _ensure_unique(commands)
    for command in commands:
        command.pop("_base", None)
        command.pop("_params", None)
        command["name"] = f"{command['group']} {command['action']}"


def is_destructive(method: str, path: str, body_names: set[str]) -> str | None:
    """Return why ``--confirm`` is required, or None when the call is safe to send."""
    parts = [part for part in path.strip("/").split("/") if part and not _is_param(part)]
    if method == "DELETE":
        return "DELETE removes data"
    if any(part in DESTRUCTIVE_SEGMENTS for part in parts):
        return "the path deletes, cancels, resets, or otherwise undoes something"
    if method == "POST" and parts:
        last = parts[-1]
        if last in {"list", "delist"} and ("vendoo-api" in parts or "native-marketplaces" in parts or last == "delist"):
            return "publishes a listing or takes one down"
        if last == "update" and "native-marketplaces" in parts:
            return "updates a live marketplace listing"
        if "price-drop" in parts and "preview" not in parts and "comps" not in parts:
            return "changes a listing price"
        if len(parts) >= 2 and parts[-2] == "updates" and last in {"apply", "download"}:
            return "downloads or applies an Anteroom update"
        if "updates" in parts and "versions" in parts and last == "install":
            return "replaces Anteroom with another published version"
        if last == "update" and "vendoo-api" in parts:
            return "writes fields onto a Vendoo item"
    if "confirm" in body_names:
        return "the API requires seller confirmation in the body"
    return None


def match_command(catalog: dict[str, Any], method: str, path: str) -> dict[str, Any] | None:
    """Match a concrete path to a catalog route template."""
    method = method.upper()
    for command in catalog.get("commands") or []:
        if command.get("method") != method:
            continue
        pattern = _template_regex(command.get("path") or "")
        if pattern.match(path):
            return command
    return None


def find_command(catalog: dict[str, Any], group: str, action: str) -> dict[str, Any] | None:
    for command in catalog.get("commands") or []:
        if command["group"] == group and command["action"] == action:
            return command
    return None


def _from_http(route: Any, method: str, operation: dict[str, Any], root: dict[str, Any], path: str) -> dict[str, Any]:
    summary, description = _text(route, operation)
    parameters = _parameters(operation, root)
    body_parameters, content_type = _body_parameters(operation, root)
    parameters.extend(body_parameters)
    body_names = {item["name"] for item in parameters if item["in"] in {"body", "form"}}
    reason = is_destructive(method, path, body_names)
    endpoint = getattr(route, "endpoint", None)
    return {
        "group": _group(route, path),
        "method": method,
        "path": path,
        "endpoint": getattr(endpoint, "__name__", "") or "",
        "operation_id": operation.get("operationId") or "",
        "summary": summary,
        "description": description,
        "parameters": parameters,
        "body_content_type": content_type,
        "response_array": _response_is_array(operation, root),
        "stream": bool(getattr(route, "is_sse_stream", False)) or _streams(endpoint),
        "websocket": False,
        "supported": True,
        "unsupported_reason": None,
        "destructive": reason is not None,
        "destructive_reason": reason,
    }


def _from_websocket(route: Any, path: str) -> dict[str, Any]:
    endpoint = getattr(route, "endpoint", None)
    summary = _doc_summary(endpoint) or "WebSocket endpoint"
    return {
        "group": _group(route, path),
        "method": "WEBSOCKET",
        "path": path,
        "endpoint": getattr(endpoint, "__name__", "") or "",
        "operation_id": "",
        "summary": summary,
        "description": (
            "Bidirectional socket. The CLI lists it so agents can see it, and does not open it. "
            f"Use the HTTP routes, or a WebSocket client against {path} on the Anteroom server."
        ),
        "parameters": [],
        "body_content_type": None,
        "response_array": False,
        "stream": False,
        "websocket": True,
        "supported": False,
        "unsupported_reason": f"WebSocket {path} is not a request/response command.",
        "destructive": False,
        "destructive_reason": None,
    }


def _from_plain(route: Any, method: str, path: str) -> dict[str, Any]:
    summary = _doc_summary(getattr(route, "endpoint", None)) or path
    return {
        "group": "docs" if path in _DOCS_PATHS else _group(route, path),
        "method": method,
        "path": path,
        "endpoint": getattr(getattr(route, "endpoint", None), "__name__", "") or "",
        "operation_id": "",
        "summary": summary,
        "description": "FastAPI built-in route.",
        "parameters": [],
        "body_content_type": None,
        "response_array": False,
        "stream": False,
        "websocket": False,
        "supported": True,
        "unsupported_reason": None,
        "destructive": False,
        "destructive_reason": None,
    }


def _group(route: Any, path: str) -> str:
    tags = [str(tag).strip().lower().replace("_", "-").replace(" ", "-") for tag in (getattr(route, "tags", None) or [])]
    if tags and tags[0]:
        name = tags[0]
    elif path in _DOCS_PATHS:
        name = "docs"
    else:
        parts = [part for part in path.strip("/").split("/") if part and part != "api" and not _is_param(part)]
        name = parts[0] if parts else "api"
    if name in RESERVED_GROUPS:
        return f"resource-{name}"
    return name


def _text(route: Any, operation: dict[str, Any]) -> tuple[str, str]:
    summary = (operation.get("summary") or "").strip()
    description = (operation.get("description") or "").strip()
    if not summary:
        summary = _doc_summary(route.endpoint) or f"{route.path}"
    if description == summary:
        description = ""
    return _one_line(summary), description


def _doc_summary(endpoint: Any) -> str:
    doc = inspect.getdoc(endpoint) if endpoint else ""
    if not doc:
        return ""
    return doc.splitlines()[0].strip()


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _parameters(operation: dict[str, Any], root: dict[str, Any]) -> list[dict[str, Any]]:
    found = []
    for parameter in operation.get("parameters") or []:
        location = parameter.get("in")
        if location not in {"path", "query"}:
            continue
        schema = _simplify(_resolve(parameter.get("schema") or {}, root))
        found.append(_field(parameter.get("name") or "", location, schema, bool(parameter.get("required")), parameter.get("description") or ""))
    return found


def _body_parameters(operation: dict[str, Any], root: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    body = operation.get("requestBody") or {}
    content = body.get("content") or {}
    if not content:
        return [], None
    if "application/json" in content:
        schema = _simplify(_resolve((content["application/json"] or {}).get("schema") or {}, root))
        return _json_fields(schema, bool(body.get("required"))), "application/json"
    if "multipart/form-data" in content:
        schema = _simplify(_resolve((content["multipart/form-data"] or {}).get("schema") or {}, root))
        return _form_fields(schema), "multipart/form-data"
    # Some routes take a raw body. Expose it as one JSON value.
    media = next(iter(content))
    return [_field("body", "body", {"type": "json"}, bool(body.get("required")), f"Raw {media} body")], media


def _json_fields(schema: dict[str, Any], body_required: bool) -> list[dict[str, Any]]:
    if schema.get("type") != "object" or not schema.get("properties"):
        required = body_required or schema.get("type") not in {None, "object"}
        return [_field("body", "body", schema or {"type": "json"}, required, schema.get("description") or "JSON body")]
    required_names = set(schema.get("required") or [])
    fields = []
    for name, child in schema["properties"].items():
        fields.append(_field(name, "body", child, name in required_names, child.get("description") or ""))
    return fields


def _form_fields(schema: dict[str, Any]) -> list[dict[str, Any]]:
    required_names = set(schema.get("required") or [])
    fields = []
    for name, child in (schema.get("properties") or {}).items():
        if _is_binary(child):
            fields.append(_field(name, "file", child, name in required_names, child.get("description") or "File upload"))
        else:
            fields.append(_field(name, "form", child, name in required_names, child.get("description") or ""))
    return fields


def _field(name: str, location: str, schema: dict[str, Any], required: bool, description: str) -> dict[str, Any]:
    items = schema.get("items") if schema.get("type") == "array" else None
    return {
        "name": name,
        "in": location,
        "type": schema.get("type") or "string",
        "required": required,
        "description": _one_line(description),
        "enum": schema.get("enum"),
        "format": schema.get("format"),
        "items": None if not isinstance(items, dict) else (items.get("type") or "string"),
        "binary": _is_binary(schema),
    }


def _is_binary(schema: dict[str, Any]) -> bool:
    if schema.get("format") == "binary" or schema.get("contentMediaType"):
        return True
    items = schema.get("items")
    if not isinstance(items, dict):
        return False
    return bool(items.get("format") == "binary" or items.get("contentMediaType"))


def _resolve(schema: Any, root: dict[str, Any], seen: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    seen = seen or set()
    if "$ref" in schema:
        ref = schema["$ref"]
        if ref in seen:
            return {"type": "object", "description": ref}
        seen.add(ref)
        node: Any = root
        for part in ref.lstrip("#/").split("/"):
            if not isinstance(node, dict) or part not in node:
                return {"type": "object"}
            node = node[part]
        resolved = _resolve(node, root, seen)
        extra = {key: value for key, value in schema.items() if key != "$ref"}
        if extra:
            return {**resolved, **extra}
        return resolved
    if "allOf" in schema:
        merged: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
        for part in schema["allOf"]:
            resolved = _resolve(part, root, seen)
            merged["properties"].update(resolved.get("properties") or {})
            merged["required"].extend(resolved.get("required") or [])
            for key in ("description", "title"):
                if resolved.get(key) and not merged.get(key):
                    merged[key] = resolved[key]
        rest = {key: value for key, value in schema.items() if key != "allOf"}
        merged.update({key: value for key, value in rest.items() if key not in {"properties", "required"}})
        if rest.get("properties"):
            merged["properties"].update(rest["properties"])
        if rest.get("required"):
            merged["required"].extend(rest["required"])
        return merged
    if "anyOf" in schema or "oneOf" in schema:
        options = []
        for part in schema.get("anyOf") or schema.get("oneOf") or []:
            resolved = _resolve(part, root, seen)
            if resolved.get("type") == "null":
                continue
            options.append(resolved)
        if len(options) == 1:
            combined = dict(options[0])
            if schema.get("description") and not combined.get("description"):
                combined["description"] = schema["description"]
            return combined
        return {"type": "json", "description": schema.get("description") or "JSON value"}
    copied = dict(schema)
    if isinstance(copied.get("properties"), dict):
        copied["properties"] = {key: _simplify(_resolve(value, root, seen)) for key, value in copied["properties"].items()}
    if isinstance(copied.get("items"), dict):
        copied["items"] = _simplify(_resolve(copied["items"], root, seen))
    return copied


def _simplify(schema: dict[str, Any]) -> dict[str, Any]:
    if not schema:
        return {"type": "string"}
    if schema.get("type"):
        return schema
    if schema.get("properties"):
        schema = dict(schema)
        schema["type"] = "object"
        return schema
    if schema.get("enum"):
        schema = dict(schema)
        schema.setdefault("type", "string")
        return schema
    if "const" in schema:
        schema = dict(schema)
        schema.setdefault("type", "string")
        schema.setdefault("enum", [schema["const"]])
        return schema
    schema = dict(schema)
    schema["type"] = "json"
    return schema


def _response_is_array(operation: dict[str, Any], root: dict[str, Any]) -> bool:
    responses = operation.get("responses") or {}
    for code in ("200", "201", "202"):
        content = ((responses.get(code) or {}).get("content") or {}).get("application/json") or {}
        schema = _simplify(_resolve(content.get("schema") or {}, root))
        if schema.get("type") == "array":
            return True
    return False


def _streams(endpoint: Any) -> bool:
    try:
        source = inspect.getsource(endpoint)
    except (OSError, TypeError):
        return False
    markers = ("StreamingResponse", "text/event-stream", "stream_generation", "stream_finished")
    return any(marker in source for marker in markers)


def _action_base(command: dict[str, Any]) -> tuple[str, list[str]]:
    parts = [part for part in command["path"].strip("/").split("/") if part]
    if parts and parts[0] == "api":
        parts = parts[1:]
    group = command["group"]
    if parts and parts[0].replace("_", "-") == group:
        parts = parts[1:]
    static = [part for part in parts if not _is_param(part) and part.replace("_", "-") != group]
    params = [_param_label(part) for part in parts if _is_param(part)]
    ends_param = bool(parts) and _is_param(parts[-1])
    if static:
        return "-".join(static), params
    return _empty_action(command, ends_param), params


def _static_tokens(command: dict[str, Any]) -> list[str]:
    base, _params = _action_base(command)
    if base in {"list", "get", "create", "replace", "update", "delete", "ws"}:
        parts = [part for part in command["path"].strip("/").split("/") if part and not _is_param(part)]
        if parts and parts[0] == "api":
            parts = parts[1:]
        if parts and parts[0].replace("_", "-") == command["group"]:
            parts = parts[1:]
        return [part for part in parts if part.replace("_", "-") != command["group"]]
    return [base]


def _empty_action(command: dict[str, Any], ends_param: bool) -> str:
    method = command["method"]
    if method == "GET" and not ends_param:
        name = command.get("endpoint") or ""
        if command.get("response_array") or name == "list" or name.startswith("list_"):
            return "list"
        return "get"
    if method == "GET":
        return "get"
    if method == "POST":
        return "create"
    if method == "PUT":
        return "replace"
    if method == "PATCH":
        return "update"
    if method == "DELETE":
        return "delete"
    if method == "WEBSOCKET":
        return "ws"
    return method.lower()


def _clean_action(action: str) -> str:
    cleaned = action.replace(".", "-").replace("/", "-").lower()
    cleaned = re.sub(r"[^a-z0-9-]+", "-", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned or "call"


def _ensure_unique(commands: list[dict[str, Any]]) -> None:
    by_group: dict[str, list[dict[str, Any]]] = {}
    for command in commands:
        by_group.setdefault(command["group"], []).append(command)
    for items in by_group.values():
        used: dict[str, dict[str, Any]] = {}
        ordered = sorted(items, key=lambda item: (item["path"].count("/"), item["path"], item["method"]))
        for command in ordered:
            action = command["action"]
            if action not in used:
                used[action] = command
                continue
            verb = _VERBS.get(command["method"], command["method"].lower())
            candidate = action if action.endswith(f"-{verb}") else f"{action}-{verb}"
            if candidate in used:
                leaf = (command.get("endpoint") or "route").replace("_", "-")
                candidate = f"{candidate}-{leaf}"
            candidate = _clean_action(candidate)
            suffix = 2
            base = candidate
            while candidate in used:
                candidate = f"{base}-{suffix}"
                suffix += 1
            command["action"] = candidate
            used[candidate] = command


def _is_param(part: str) -> bool:
    return part.startswith("{") and part.endswith("}")


def _param_label(part: str) -> str:
    name = part[1:-1]
    name = name.split(":", 1)[0]
    return name.replace("_", "-")


def _template_regex(path: str) -> re.Pattern[str]:
    pieces = []
    for part in path.strip("/").split("/"):
        if _is_param(part):
            pieces.append(r"[^/]+")
        else:
            pieces.append(re.escape(part))
    return re.compile("^/" + "/".join(pieces) + "/?$")


def command_help(command: dict[str, Any]) -> str:
    """Long help for one generated command. Percent signs are escaped for argparse."""
    lines = [command.get("summary") or command["path"], "", f"{command['method']} {command['path']}"]
    if command.get("description"):
        lines.extend(["", command["description"]])
    if command.get("destructive"):
        lines.extend(["", f"Destructive: {command['destructive_reason']}. Re-run with --confirm to send it."])
    if command.get("stream"):
        lines.extend(["", "May answer with a server-sent event stream. Reading stops after --stream-seconds (default 20; 0 reads until the server closes, still bounded by --timeout)."])
    if not command.get("supported"):
        lines.extend(["", command.get("unsupported_reason") or "This route is not callable from the CLI."])
    params = command.get("parameters") or []
    if params:
        lines.extend(["", "Parameters:"])
        for parameter in params:
            need = "required" if parameter.get("required") else "optional"
            kind = parameter.get("type") or "string"
            if parameter.get("items"):
                kind = f"array of {parameter['items']}"
            lines.append(f"  --{_flag(parameter['name'])}  ({parameter['in']}, {kind}, {need}) {parameter.get('description') or ''}".rstrip())
    text = "\n".join(lines)
    return text.replace("%", "%%")


def _flag(name: str) -> str:
    return name.replace("_", "-")


def require_catalog() -> dict[str, Any]:
    try:
        return load_catalog()
    except Exception as exc:
        raise UsageError(
            "Could not read Anteroom's routes. Install the app (pip install -e . from vendoo-studio) and retry. "
            f"({exc})"
        ) from exc
