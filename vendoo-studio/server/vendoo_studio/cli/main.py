"""``anteroom`` command line.

Commands for the HTTP API are built from the FastAPI route catalog. ``auth``,
``api``, ``commands``, ``mcp``, and ``version`` are the fixed commands around
that catalog.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from vendoo_studio.cli.catalog import command_help, load_catalog, require_catalog
from vendoo_studio.cli.client import AnteroomClient
from vendoo_studio.cli.config import (
    base_url_warning,
    config_path,
    load_config,
    login,
    logout,
    public_environments,
    resolve_target,
    use,
)
from vendoo_studio.cli.constants import (
    AUTH_NOTE,
    DEFAULT_STREAM_SECONDS,
    DEFAULT_TIMEOUT,
    EXIT_API,
    EXIT_CONFIRM,
    EXIT_CONNECT,
    EXIT_OK,
    EXIT_USAGE,
)
from vendoo_studio.cli.errors import ApiError, ConfirmRequired, ConnectFailed, UsageError
from vendoo_studio.cli.execute import dest_name, flag_name, parse_bool, run_command, run_raw
from vendoo_studio.cli.output import emit, envelope, note

_GLOBAL_FLAGS = frozenset({
    "json", "confirm", "env", "base-url", "timeout", "stream-seconds", "output", "header", "body", "help",
})


def main(argv: list[str] | None = None, *, transport=None) -> int:
    try:
        return _main(argv, transport=transport)
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            return EXIT_OK
        return EXIT_OK
    except UsageError as exc:
        note(str(exc))
        if _wants_json(argv):
            emit(envelope(ok=False, status=None, error=str(exc)), as_json=True)
        return EXIT_USAGE
    except ConfirmRequired as exc:
        message = f"This command is destructive ({exc.reason}). Re-run with --confirm to send it."
        if _wants_json(argv):
            emit(envelope(ok=False, status=None, error=message, reason=exc.reason), as_json=True)
        else:
            note(message)
        return EXIT_CONFIRM
    except ConnectFailed as exc:
        if _wants_json(argv):
            emit(envelope(ok=False, status=None, error=str(exc)), as_json=True)
        else:
            note(str(exc))
        return EXIT_CONNECT
    except ApiError as exc:
        payload = envelope(ok=False, status=exc.status, data=exc.data, error=exc.message)
        if _wants_json(argv):
            emit(payload, as_json=True)
        else:
            note(f"HTTP {exc.status}: {exc.message}")
            emit(payload, as_json=False)
        return EXIT_API


def _main(argv: list[str] | None, *, transport) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv in (["-h"], ["--help"]):
        catalog = _catalog_or_empty()
        parser = build_parser(catalog)
        parser.print_help()
        return EXIT_OK if argv else EXIT_USAGE
    catalog = require_catalog()
    parser = build_parser(catalog)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return int(code) if isinstance(code, int) else EXIT_USAGE
    args.transport = transport
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return EXIT_USAGE
    return handler(args, catalog)


def build_parser(catalog: dict[str, Any]) -> argparse.ArgumentParser:
    # Subparsers set the same flags with SUPPRESS so a value parsed on an outer
    # parser (--json before the command) is not replaced by the inner default.
    common = _common(False)
    nested = _common(True)
    parser = argparse.ArgumentParser(
        prog="anteroom",
        description=(
            "Control Anteroom from the terminal. Commands are generated from the app's HTTP routes.\n\n"
            + AUTH_NOTE
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[common],
    )
    parser.set_defaults(handler=None)
    sub = parser.add_subparsers(dest="group", metavar="group")

    _add_auth(sub, nested)
    _add_api(sub, nested)
    _add_commands(sub, nested)
    _add_mcp(sub, nested)
    version = sub.add_parser("version", help="Print the Anteroom version", parents=[nested])
    version.set_defaults(handler=_cmd_version)

    for group, commands in _by_group(catalog).items():
        group_parser = sub.add_parser(group, help=f"{group.replace('-', ' ')} routes", parents=[nested])
        actions = group_parser.add_subparsers(dest="action", required=True, metavar="action")
        for command in commands:
            action = actions.add_parser(
                command["action"],
                help=_short(command),
                description=command_help(command),
                formatter_class=argparse.RawDescriptionHelpFormatter,
                parents=[nested],
            )
            _add_flags(action, command)
            action.set_defaults(handler=_cmd_http, command=command)
    return parser


def _common(suppress: bool) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    missing = argparse.SUPPRESS if suppress else None
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS if suppress else False, help="Print a JSON object on stdout")
    parser.add_argument("--confirm", action="store_true", default=argparse.SUPPRESS if suppress else False, help="Send a destructive command (delete, publish, delist, and similar)")
    parser.add_argument("--env", dest="environment", default=missing, help="Environment alias to use for this command")
    parser.add_argument("--base-url", default=missing, help="Anteroom base URL for this command (overrides the saved environment)")
    parser.add_argument("--timeout", type=float, default=missing, help=f"Request timeout in seconds (default {DEFAULT_TIMEOUT:g})")
    parser.add_argument("--stream-seconds", type=float, default=missing, help=f"How long to read a server-sent event stream (default {DEFAULT_STREAM_SECONDS:g}; 0 reads until the server closes)")
    parser.add_argument("--output", default=missing, help="Write the raw response body to this file")
    parser.add_argument("--header", action="append", default=missing, help="Extra request header, as 'Name: value'. Repeatable.")
    parser.add_argument("--body", default=missing, help="JSON body, @path to a file, or - to read stdin")
    return parser


def _add_auth(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    auth = sub.add_parser("auth", help="Save Anteroom base URLs and optional tokens", parents=[common])
    actions = auth.add_subparsers(dest="action", required=True, metavar="action")
    login_parser = actions.add_parser("login", help="Save an environment and make it current", parents=[common])
    login_parser.add_argument("--alias", default="local", help="Environment name (default local)")
    login_parser.add_argument("--token", help="Bearer token sent as Authorization. The app ignores it unless a proxy checks it.")
    login_parser.add_argument("--clear-token", action="store_true", help="Remove a stored token for this alias")
    login_parser.set_defaults(handler=_cmd_login)
    logout_parser = actions.add_parser("logout", help="Remove a saved environment", parents=[common])
    logout_parser.add_argument("--alias", required=True)
    logout_parser.set_defaults(handler=_cmd_logout)
    use_parser = actions.add_parser("use", help="Select the environment later commands use", parents=[common])
    use_parser.add_argument("alias")
    use_parser.set_defaults(handler=_cmd_use)
    listed = actions.add_parser("list", help="List saved environments", parents=[common])
    listed.set_defaults(handler=_cmd_auth_list)
    who = actions.add_parser("whoami", help="Show the current environment and call /api/health", parents=[common])
    who.set_defaults(handler=_cmd_whoami)


def _add_api(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    api = sub.add_parser("api", help="Call a path directly, or print the OpenAPI schema", parents=[common])
    actions = api.add_subparsers(dest="action", required=True, metavar="action")
    request = actions.add_parser("request", help="METHOD PATH, for a route that has no generated command yet", parents=[common])
    request.add_argument("method")
    request.add_argument("path")
    request.add_argument("--query", action="append", default=None, help="Query parameter as key=value. Repeatable.")
    request.set_defaults(handler=_cmd_request)
    schema = actions.add_parser("schema", help="Print the OpenAPI schema from the local Anteroom code", parents=[common])
    schema.set_defaults(handler=_cmd_schema)


def _add_commands(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    commands = sub.add_parser("commands", help="List every command. Use --json for the machine-readable catalog.", parents=[common])
    commands.set_defaults(handler=_cmd_commands)


def _add_mcp(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    mcp = sub.add_parser("mcp", help="Serve the same commands over MCP for an agent", parents=[common])
    actions = mcp.add_subparsers(dest="action", required=True, metavar="action")
    serve = actions.add_parser("serve", help="Speak MCP on stdin/stdout", parents=[common])
    serve.set_defaults(handler=_cmd_mcp)


def _add_flags(parser: argparse.ArgumentParser, command: dict[str, Any]) -> None:
    used = set(_GLOBAL_FLAGS)
    for parameter in command.get("parameters") or []:
        if parameter["name"] == "confirm" and parameter["in"] == "body":
            continue
        option = flag_name(parameter["name"])
        if option in used:
            option = f"{parameter['in']}-{option}"
        used.add(option)
        kwargs: dict[str, Any] = {
            "dest": dest_name(_prefix(parameter["in"]), parameter["name"]),
            "help": _param_help(parameter),
        }
        if parameter.get("required") and parameter["in"] in {"path", "query", "file", "form"}:
            kwargs["required"] = True
        kind = parameter.get("type") or "string"
        if parameter["in"] == "file":
            kwargs["action"] = "append" if kind == "array" else "store"
            parser.add_argument(f"--{option}", **kwargs)
            continue
        if kind == "boolean":
            parser.add_argument(f"--{option}", action=argparse.BooleanOptionalAction, default=None, **{key: value for key, value in kwargs.items() if key != "required"})
            continue
        if kind == "array" and (parameter.get("items") or "string") in {"string", "integer", "number", "boolean"}:
            kwargs["action"] = "append"
            kwargs["type"] = _arg_type(parameter.get("items") or "string")
            parser.add_argument(f"--{option}", **kwargs)
            continue
        if kind == "array":
            kwargs["action"] = "append"
            parser.add_argument(f"--{option}", **kwargs)
            continue
        if kind in {"integer", "number"}:
            kwargs["type"] = _arg_type(kind)
        if parameter.get("enum") and kind == "string":
            kwargs["choices"] = [str(item) for item in parameter["enum"]]
        parser.add_argument(f"--{option}", **kwargs)


def _cmd_http(args: argparse.Namespace, catalog: dict[str, Any]) -> int:
    del catalog
    client = _client(args)
    if args.stream_seconds is None and args.command.get("stream"):
        args.stream_seconds = DEFAULT_STREAM_SECONDS
    result = run_command(args.command, args, client)
    payload = envelope(ok=True, status=result["status"], data=result["data"], command=args.command["name"])
    emit(payload, as_json=args.json)
    return EXIT_OK


def _cmd_request(args: argparse.Namespace, catalog: dict[str, Any]) -> int:
    client = _client(args)
    result = run_raw(args.method, args.path, args, client, catalog)
    payload = envelope(ok=True, status=result["status"], data=result["data"], method=args.method.upper(), path=args.path)
    emit(payload, as_json=args.json)
    return EXIT_OK


def _cmd_schema(args: argparse.Namespace, catalog: dict[str, Any]) -> int:
    del catalog
    from vendoo_studio.main import app

    schema = app.openapi()
    if args.json:
        emit(envelope(ok=True, status=200, data=schema), as_json=True)
    else:
        emit({}, as_json=False, human=json.dumps(schema, indent=2))
    return EXIT_OK


def _cmd_commands(args: argparse.Namespace, catalog: dict[str, Any]) -> int:
    if args.json:
        emit(catalog, as_json=True)
        return EXIT_OK
    lines = [AUTH_NOTE, ""]
    lines.append("auth login    Save a base URL as an environment")
    lines.append("auth list     Show saved environments")
    lines.append("auth use      Select an environment")
    lines.append("auth logout   Remove an environment (--confirm)")
    lines.append("auth whoami   Call /api/health on the current environment")
    lines.append("api request   METHOD PATH")
    lines.append("api schema    OpenAPI schema from the local code")
    lines.append("commands      This list")
    lines.append("mcp serve     MCP server on stdin/stdout")
    lines.append("version")
    lines.append("")
    current = None
    for command in catalog.get("commands") or []:
        if command["group"] != current:
            current = command["group"]
            lines.append(f"[{current}]")
        mark = " !" if command.get("destructive") else ""
        skip = " (not callable)" if not command.get("supported", True) else ""
        summary = command.get("summary") or ""
        lines.append(f"  {command['action']:<40} {command['method']:<10} {command['path']}{mark}{skip}  {summary}")
    emit({}, as_json=False, human="\n".join(lines))
    return EXIT_OK


def _cmd_login(args: argparse.Namespace, catalog: dict[str, Any]) -> int:
    del catalog
    if not args.base_url:
        raise UsageError("Pass --base-url, for example http://127.0.0.1:4318")
    data = login(args.alias, args.base_url, args.token, clear_token=bool(args.clear_token))
    warning = base_url_warning(args.base_url)
    if warning:
        note(warning)
    payload = {"ok": True, "status": None, "current": data["current"], "environments": public_environments(data)}
    emit(payload, as_json=args.json, human=_format_envs(payload))
    return EXIT_OK


def _cmd_logout(args: argparse.Namespace, catalog: dict[str, Any]) -> int:
    del catalog
    if not args.confirm:
        raise ConfirmRequired("logout removes a saved environment")
    data = logout(args.alias)
    payload = {"ok": True, "status": None, "current": data.get("current"), "environments": public_environments(data)}
    emit(payload, as_json=args.json, human=_format_envs(payload))
    return EXIT_OK


def _cmd_use(args: argparse.Namespace, catalog: dict[str, Any]) -> int:
    del catalog
    data = use(args.alias)
    payload = {"ok": True, "status": None, "current": data["current"], "environments": public_environments(data)}
    emit(payload, as_json=args.json, human=_format_envs(payload))
    return EXIT_OK


def _cmd_auth_list(args: argparse.Namespace, catalog: dict[str, Any]) -> int:
    del catalog
    data = load_config()
    payload = {"ok": True, "status": None, "current": data.get("current"), "environments": public_environments(data), "config": str(config_path())}
    saved = payload["config"]
    human = _format_envs(payload) + f"\nConfig file: {saved}"
    emit(payload, as_json=args.json, human=human)
    return EXIT_OK


def _cmd_whoami(args: argparse.Namespace, catalog: dict[str, Any]) -> int:
    del catalog
    client = _client(args)
    result = client.call("GET", "/api/health")
    target = resolve_target(environment=args.environment, base_url=args.base_url)
    payload = envelope(
        ok=True, status=result["status"], data=result["data"],
        environment=target.alias, base_url=target.base_url, token=bool(target.token),
    )
    human = f"{target.alias}  {target.base_url}  token={'yes' if target.token else 'no'}\n" + json.dumps(result["data"], indent=2)
    emit(payload, as_json=args.json, human=human)
    return EXIT_OK


def _cmd_version(args: argparse.Namespace, catalog: dict[str, Any]) -> int:
    del catalog
    version = _read_version()
    emit(envelope(ok=True, status=None, data={"version": version}), as_json=args.json, human=version)
    return EXIT_OK


def _cmd_mcp(args: argparse.Namespace, catalog: dict[str, Any]) -> int:
    del catalog
    from vendoo_studio.cli.mcp_server import serve
    return serve(transport=getattr(args, "transport", None))


def _client(args: argparse.Namespace) -> AnteroomClient:
    target = resolve_target(environment=args.environment, base_url=args.base_url)
    warning = base_url_warning(target.base_url)
    if warning:
        note(warning)
    timeout = args.timeout if args.timeout is not None else DEFAULT_TIMEOUT
    return AnteroomClient(target.base_url, target.token, timeout=timeout, transport=getattr(args, "transport", None))


def _by_group(catalog: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for command in catalog.get("commands") or []:
        grouped.setdefault(command["group"], []).append(command)
    return dict(sorted(grouped.items()))


def _prefix(location: str) -> str:
    return {"path": "path", "query": "query", "body": "body", "form": "form", "file": "file"}.get(location, location)


def _param_help(parameter: dict[str, Any]) -> str:
    need = "required" if parameter.get("required") else "optional"
    text = f"{parameter['in']}, {parameter.get('type') or 'string'}, {need}"
    if parameter.get("description"):
        text = f"{text}. {parameter['description']}"
    return text.replace("%", "%%")


def _arg_type(kind: str):
    if kind == "integer":
        return int
    if kind == "number":
        return float
    if kind == "boolean":
        return parse_bool
    return str


def _short(command: dict[str, Any]) -> str:
    text = command.get("summary") or command["path"]
    text = text.replace("%", "%%")
    if command.get("destructive"):
        text = f"{text} (destructive)"
    if len(text) > 88:
        return text[:85] + "..."
    return text


def _format_envs(payload: dict[str, Any]) -> str:
    lines = []
    for row in payload.get("environments") or []:
        mark = " current" if row.get("current") else ""
        token = "token=yes" if row.get("token") else "token=no"
        lines.append(f"{row['alias']}{mark}  {row['base_url']}  {token}")
    if not lines:
        lines.append("No environments saved.")
    return "\n".join(lines)


def _read_version() -> str:
    from pathlib import Path
    path = Path(__file__).resolve().parents[3] / "VERSION"
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"
    return text or "0.0.0"


def _catalog_or_empty() -> dict[str, Any]:
    try:
        return load_catalog()
    except Exception:
        return {"commands": [], "skipped": [], "auth": AUTH_NOTE}


def _wants_json(argv: list[str] | None) -> bool:
    # The console script calls main() with no argv. Handlers then see sys.argv,
    # and the error path has to see the same --json or it prints human text.
    source = sys.argv[1:] if argv is None else argv
    return "--json" in source
