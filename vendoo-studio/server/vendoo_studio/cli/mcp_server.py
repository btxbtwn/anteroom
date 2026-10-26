"""Minimal MCP server over stdio.

One JSON-RPC message per line. Logs stay on stderr so a client can read stdout.
Each supported HTTP command is a tool. WebSocket routes stay in
``anteroom_commands`` and are not tools, because this server cannot open them.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from typing import Any

from vendoo_studio.cli.catalog import load_catalog
from vendoo_studio.cli.client import AnteroomClient
from vendoo_studio.cli.config import resolve_target
from vendoo_studio.cli.constants import DEFAULT_STREAM_SECONDS, DEFAULT_TIMEOUT
from vendoo_studio.cli.errors import ApiError, ConfirmRequired, ConnectFailed, UsageError
from vendoo_studio.cli.execute import arguments_to_namespace, run_command, run_raw
from vendoo_studio.cli.output import envelope


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

_PROTOCOL = "2024-11-05"
_SUPPORTED = {"2024-11-05", "2025-03-26", "2025-06-18"}


def serve(stdin=None, stdout=None, *, transport=None) -> int:
    incoming = stdin or sys.stdin
    outgoing = stdout or sys.stdout
    for line in incoming:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            _write(outgoing, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        response = handle_message(message, transport=transport)
        if response is not None:
            _write(outgoing, response)
    return 0


def handle_message(message: dict[str, Any], *, transport=None) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
    method = message.get("method")
    msg_id = message.get("id")
    if method and msg_id is None:
        return None
    try:
        result = _dispatch(method, message.get("params") or {}, transport=transport)
    except RpcError as exc:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": exc.code, "message": exc.message}}
    except ConfirmRequired as exc:
        if method == "tools/call":
            return _tool_error(msg_id, f"Confirmation required: {exc.reason}. Pass confirm=true.")
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32603, "message": str(exc)}}
    except (UsageError, ApiError, ConnectFailed) as exc:
        if method == "tools/call":
            return _tool_error(msg_id, str(exc))
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32603, "message": str(exc)}}
    except Exception as exc:  # noqa: BLE001 - one failed tool call must not kill the server
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32603, "message": str(exc)}}
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _dispatch(method: str | None, params: dict[str, Any], *, transport) -> Any:
    if method == "initialize":
        version = str(params.get("protocolVersion") or _PROTOCOL)
        if version not in _SUPPORTED:
            version = _PROTOCOL
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "anteroom", "version": _version()},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": _tools(load_catalog())}
    if method == "tools/call":
        name = params.get("name") or ""
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise UsageError("Tool arguments must be an object")
        return _call_tool(name, arguments, transport=transport)
    raise RpcError(-32601, f"Unknown method {method}")


def _tools(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    tools = [
        {
            "name": "anteroom_commands",
            "description": "List every Anteroom CLI command, including routes the CLI cannot call, with method, path, parameters, and whether --confirm is required.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "anteroom_api_request",
            "description": "Call any Anteroom HTTP path. Destructive paths and confirm:true still require confirm=true. " + catalog.get("auth", ""),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                    "path": {"type": "string", "description": "Absolute path, for example /api/health"},
                    "body": {"type": "object"},
                    "query": {"type": "object"},
                    "confirm": {"type": "boolean"},
                    "stream_seconds": {"type": "number"},
                },
                "required": ["method", "path"],
            },
        },
        {
            "name": "anteroom_auth_whoami",
            "description": "Show the selected environment and GET /api/health.",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]
    seen = {tool["name"] for tool in tools}
    for command in catalog.get("commands") or []:
        if not command.get("supported", True):
            continue
        name = tool_name(command)
        if name in seen:
            continue
        seen.add(name)
        tools.append({
            "name": name,
            "description": _tool_description(command),
            "inputSchema": _input_schema(command),
        })
    return tools


def tool_name(command: dict[str, Any]) -> str:
    raw = f"{command['group']}__{command['action']}"
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", raw)
    if len(cleaned) <= 64:
        return cleaned
    digest = hashlib.sha256(cleaned.encode()).hexdigest()[:8]
    return cleaned[:55].rstrip("_-") + "_" + digest


def _tool_description(command: dict[str, Any]) -> str:
    text = command.get("summary") or command["path"]
    text = f"{text} ({command['method']} {command['path']})"
    if command.get("destructive"):
        text += f" Destructive: {command['destructive_reason']}. Requires confirm=true."
    if command.get("stream"):
        text += " May stream events; pass stream_seconds."
    return text


def _input_schema(command: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for parameter in command.get("parameters") or []:
        if parameter["name"] == "confirm" and parameter["in"] == "body":
            continue
        properties[parameter["name"]] = _parameter_schema(parameter)
        if parameter.get("required"):
            required.append(parameter["name"])
    properties["body"] = {
        "type": "object",
        "description": "JSON object merged over the fields above. Use this for nested values.",
    }
    if command.get("stream"):
        properties["stream_seconds"] = {"type": "number", "description": "Stop reading a stream after this many seconds. 0 reads until the server closes."}
    if command.get("destructive"):
        properties["confirm"] = {
            "type": "boolean",
            "description": "Must be true. " + (command.get("destructive_reason") or ""),
        }
        required.append("confirm")
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _parameter_schema(parameter: dict[str, Any]) -> dict[str, Any]:
    kind = parameter.get("type") or "string"
    schema: dict[str, Any]
    if kind == "array":
        schema = {"type": "array", "items": {"type": parameter.get("items") or "string"}}
    elif kind in {"integer", "number", "boolean"}:
        schema = {"type": kind}
    elif kind in {"object", "json"}:
        schema = {"type": "object" if kind == "object" else {}}
        if kind == "json":
            schema = {"description": "JSON value"}
    else:
        schema = {"type": "string"}
    if parameter.get("enum"):
        schema["enum"] = parameter["enum"]
    if parameter.get("description"):
        schema["description"] = f"{parameter['in']}: {parameter['description']}"
    else:
        schema["description"] = parameter["in"]
    if parameter["in"] == "file":
        schema = {"type": "string" if parameter.get("type") != "array" else "array", "description": "Path to a file on this machine"}
        if parameter.get("type") == "array":
            schema["items"] = {"type": "string"}
    return schema


def _call_tool(name: str, arguments: dict[str, Any], *, transport) -> dict[str, Any]:
    catalog = load_catalog()
    if name == "anteroom_commands":
        return _text(json.dumps(catalog, indent=2))
    target = resolve_target(environment=None, base_url=None)
    client = AnteroomClient(target.base_url, target.token, timeout=DEFAULT_TIMEOUT, transport=transport)
    if name == "anteroom_auth_whoami":
        try:
            result = client.call("GET", "/api/health")
        except (ApiError, ConnectFailed) as exc:
            raise exc
        payload = envelope(ok=True, status=result["status"], data=result["data"], environment=target.alias, base_url=target.base_url, token=bool(target.token))
        return _text(json.dumps(payload, indent=2))
    if name == "anteroom_api_request":
        namespace = argparse_namespace(arguments)
        try:
            result = run_raw(str(arguments.get("method") or ""), str(arguments.get("path") or ""), namespace, client, catalog)
        except ApiError as exc:
            return _text(json.dumps(envelope(ok=False, status=exc.status, data=exc.data, error=exc.message), indent=2), error=True)
        return _text(json.dumps(envelope(ok=True, status=result["status"], data=result["data"]), indent=2))
    command = _command_for_tool(catalog, name)
    if command is None:
        raise UsageError(f"Unknown tool {name}. Call anteroom_commands.")
    if command.get("destructive") and arguments.get("confirm") is not True:
        raise ConfirmRequired(command.get("destructive_reason") or "this command is destructive")
    namespace = arguments_to_namespace(command, arguments)
    if namespace.stream_seconds is None and command.get("stream"):
        namespace.stream_seconds = DEFAULT_STREAM_SECONDS
    try:
        result = run_command(command, namespace, client)
    except ApiError as exc:
        return _text(json.dumps(envelope(ok=False, status=exc.status, data=exc.data, error=exc.message), indent=2), error=True)
    return _text(json.dumps(envelope(ok=True, status=result["status"], data=result["data"], command=command["name"]), indent=2))


def argparse_namespace(arguments: dict[str, Any]):
    import argparse
    body = arguments.get("body")
    return argparse.Namespace(
        confirm=arguments.get("confirm") is True,
        body=json.dumps(body) if isinstance(body, (dict, list)) else body,
        header=None,
        output=arguments.get("output"),
        stream_seconds=arguments.get("stream_seconds", DEFAULT_STREAM_SECONDS),
        query=_query_items(arguments.get("query")),
    )


def _query_items(query: Any) -> list[str] | None:
    if not isinstance(query, dict):
        return None
    items = []
    for key, value in query.items():
        if isinstance(value, list):
            for item in value:
                items.append(f"{key}={item}")
        else:
            items.append(f"{key}={value}")
    return items


def _command_for_tool(catalog: dict[str, Any], name: str) -> dict[str, Any] | None:
    for command in catalog.get("commands") or []:
        if tool_name(command) == name:
            return command
    return None


def _text(text: str, *, error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def _tool_error(msg_id: Any, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": _text(message, error=True)}


def _write(outgoing, payload: dict[str, Any]) -> None:
    outgoing.write(json.dumps(payload, default=str) + "\n")
    outgoing.flush()


def _version() -> str:
    path = __import__("pathlib").Path(__file__).resolve().parents[3] / "VERSION"
    try:
        return path.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"
