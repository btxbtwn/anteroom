"""The anteroom CLI: catalog coverage, confirm gate, JSON output, auth, and MCP."""

from __future__ import annotations

import gzip
import json
import os
import stat
import sys
from io import StringIO
from pathlib import Path

import httpx
import pytest

from vendoo_studio.cli.catalog import is_destructive
from vendoo_studio.cli.main import main


@pytest.fixture(scope="module")
def catalog_file(tmp_path_factory):
    from vendoo_studio.cli.catalog import build_catalog_from_app

    catalog = build_catalog_from_app()
    path = tmp_path_factory.mktemp("anteroom-catalog") / "catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    previous = os.environ.get("ANTEROOM_CATALOG_FILE")
    os.environ["ANTEROOM_CATALOG_FILE"] = str(path)
    yield path, catalog
    if previous is None:
        os.environ.pop("ANTEROOM_CATALOG_FILE", None)
    else:
        os.environ["ANTEROOM_CATALOG_FILE"] = previous


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTEROOM_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("ANTEROOM_BASE_URL", raising=False)
    monkeypatch.delenv("ANTEROOM_TOKEN", raising=False)
    monkeypatch.delenv("ANTEROOM_ENV", raising=False)
    return tmp_path / "config"


def _transport(handler):
    return httpx.MockTransport(handler)


def test_destructive_rules_cover_publish_and_leave_reads_alone():
    assert is_destructive("DELETE", "/api/boxes/1", set())
    assert is_destructive("POST", "/api/conversations/c/vendoo-api/list", {"marketplaces", "confirm"})
    assert is_destructive("POST", "/api/conversations/c/vendoo-api/delist", set())
    assert is_destructive("POST", "/api/conversations/c/native-marketplaces/ebay/update", set())
    assert is_destructive("POST", "/api/conversations/c/price-drop", set())
    assert is_destructive("POST", "/api/updates/apply", set())
    assert is_destructive("POST", "/api/updates/versions/install", set())
    assert is_destructive("GET", "/api/health", set()) is None
    assert is_destructive("POST", "/api/conversations/c/price-drop/preview", set()) is None
    assert is_destructive("POST", "/api/conversations", set()) is None
    assert is_destructive("GET", "/api/jobs", set()) is None


def test_catalog_matches_every_route(catalog_file):
    from fastapi.routing import APIRoute, APIWebSocketRoute, iter_route_contexts
    from starlette.routing import Mount, Route, WebSocketRoute

    from vendoo_studio.main import app

    _path, catalog = catalog_file
    expected = set()
    mounts = []
    for context in iter_route_contexts(app.routes):
        route = context.original_route
        path = context.path_format or context.path or getattr(route, "path", None)
        if isinstance(route, APIRoute):
            for method in (context.methods or set()) - {"HEAD", "OPTIONS"}:
                expected.add((method, path))
        elif isinstance(route, (APIWebSocketRoute, WebSocketRoute)):
            expected.add(("WEBSOCKET", path))
        elif isinstance(route, Mount):
            mounts.append(path or "/")
        elif isinstance(route, Route):
            for method in set(context.methods or route.methods or []) - {"HEAD", "OPTIONS"}:
                expected.add((method, path))
    got = {(command["method"], command["path"]) for command in catalog["commands"]}
    assert got == expected
    assert len(catalog["commands"]) > 200
    names = [command["name"] for command in catalog["commands"]]
    assert len(names) == len(set(names))
    by_path = {(command["method"], command["path"]): command for command in catalog["commands"]}
    assert by_path[("GET", "/api/health")]["name"] == "health get"
    assert by_path[("GET", "/api/status")]["name"] == "health status"
    assert by_path[("GET", "/api/boxes")]["name"] == "boxes list"
    assert by_path[("POST", "/api/boxes")]["name"] == "boxes create"
    assert by_path[("DELETE", "/api/boxes/{box_id}")]["name"] == "boxes delete"
    assert by_path[("GET", "/api/conversations")]["name"] == "conversations list"
    assert by_path[("DELETE", "/api/conversations/{conv_id}")]["destructive"] is True
    publish = by_path[("POST", "/api/conversations/{conv_id}/vendoo-api/list")]
    assert publish["destructive"] is True
    assert any(item["name"] == "marketplaces" and item["required"] for item in publish["parameters"])
    assert by_path[("WEBSOCKET", "/api/extension/ws")]["supported"] is False
    assert {item["path"] for item in catalog["skipped"]} == set(mounts)


def test_health_get_json(catalog_file, config_dir, capsys):
    del catalog_file, config_dir

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/health"
        assert request.headers["user-agent"].startswith("anteroom-cli")
        return httpx.Response(200, json={"status": "ok", "version": "9.9.9"})

    code = main(["health", "get", "--json"], transport=_transport(handler))
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["status"] == 200
    assert payload["data"]["version"] == "9.9.9"
    assert captured.err == ""


def test_gender_check_sends_an_empty_body_when_apply_is_left_off(catalog_file, config_dir, capsys):
    del catalog_file, config_dir
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        seen["path"] = request.url.path
        return httpx.Response(200, json={"ok": True, "department": "Men", "applied": False})

    code = main(
        ["conversations", "department-review", "--conv-id", "c1", "--json"],
        transport=_transport(handler),
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["data"]["department"] == "Men"
    assert seen["path"] == "/api/conversations/c1/department/review"
    assert seen["body"] == b"{}"


def test_api_error_exit_code(catalog_file, config_dir, capsys):
    del catalog_file, config_dir

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Conversation not found"})

    code = main(["conversations", "get", "--conv-id", "missing", "--json"], transport=_transport(handler))
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["ok"] is False
    assert payload["status"] == 404
    assert payload["error"] == "Conversation not found"


def test_connection_error(catalog_file, config_dir, capsys):
    del catalog_file, config_dir

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    code = main(["health", "get", "--json"], transport=_transport(handler))
    payload = json.loads(capsys.readouterr().out)
    assert code == 3
    assert payload["ok"] is False
    assert "Could not reach" in payload["error"]


def test_unknown_command_and_missing_path_param(catalog_file, config_dir, capsys):
    del catalog_file, config_dir
    assert main(["nope"], transport=_transport(lambda request: httpx.Response(500))) == 2
    capsys.readouterr()
    assert main(["conversations", "get", "--json"], transport=_transport(lambda request: httpx.Response(500))) == 2


def test_delete_and_publish_require_confirm(catalog_file, config_dir, capsys):
    del catalog_file, config_dir
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        called["body"] = json.loads(request.content.decode() or "null")
        called["path"] = request.url.path
        return httpx.Response(200, json={"ok": True})

    code = main(["boxes", "delete", "--box-id", "b1", "--json"], transport=_transport(handler))
    payload = json.loads(capsys.readouterr().out)
    assert code == 4
    assert called["n"] == 0
    assert payload["ok"] is False

    code = main(
        ["vendoo-api", "conversations-list", "--conv-id", "c1", "--marketplaces", "ebay", "--json"],
        transport=_transport(handler),
    )
    assert code == 4
    assert called["n"] == 0

    code = main(
        ["vendoo-api", "conversations-list", "--conv-id", "c1", "--body", '{"confirm": true, "marketplaces": ["ebay"]}', "--json"],
        transport=_transport(handler),
    )
    assert code == 4
    assert called["n"] == 0

    code = main(
        ["vendoo-api", "conversations-list", "--conv-id", "c1", "--marketplaces", "ebay", "--marketplaces", "etsy", "--confirm", "--json"],
        transport=_transport(handler),
    )
    assert code == 0
    assert called["n"] == 1
    assert called["path"] == "/api/conversations/c1/vendoo-api/list"
    assert called["body"]["confirm"] is True
    assert called["body"]["marketplaces"] == ["ebay", "etsy"]


def test_api_request_escape_hatch(catalog_file, config_dir, capsys):
    del catalog_file, config_dir

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/health"
        assert request.url.params["verbose"] == "1"
        return httpx.Response(200, json={"status": "ok"})

    code = main(
        ["api", "request", "GET", "/api/health", "--query", "verbose=1", "--json"],
        transport=_transport(handler),
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["data"]["status"] == "ok"

    code = main(["api", "request", "DELETE", "/api/boxes/b1", "--json"], transport=_transport(handler))
    assert code == 4


def test_api_request_reads_gzip_json_and_still_streams_events(catalog_file, config_dir, capsys):
    del catalog_file, config_dir
    payload = {"version": "1.2.3", "entries": ["note " * 400]}
    raw = json.dumps(payload).encode()

    def gzip_json(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/changelog"
        return httpx.Response(
            200,
            headers={"content-type": "application/json", "content-encoding": "gzip"},
            content=gzip.compress(raw),
        )

    code = main(
        ["api", "request", "GET", "/api/changelog", "--json", "--base-url", "http://anteroom.test"],
        transport=_transport(gzip_json),
    )
    body = json.loads(capsys.readouterr().out)
    assert code == 0
    assert body["data"]["version"] == "1.2.3"

    code = main(
        ["api", "request", "GET", "/api/changelog", "--json", "--base-url", "http://anteroom.test", "--stream-seconds", "5"],
        transport=_transport(gzip_json),
    )
    body = json.loads(capsys.readouterr().out)
    assert code == 0
    assert body["data"]["version"] == "1.2.3"

    def stream(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/jobs/j1/preview"
        text = 'data: {"step": 1}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=text.encode())

    code = main(
        ["api", "request", "GET", "/api/jobs/j1/preview", "--json", "--base-url", "http://anteroom.test"],
        transport=_transport(stream),
    )
    body = json.loads(capsys.readouterr().out)
    assert code == 0
    assert body["data"]["events"] == [{"step": 1}, {"done": True}]


def test_process_argv_errors_honor_json(catalog_file, config_dir, capsys, monkeypatch):
    del catalog_file, config_dir

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(sys, "argv", ["anteroom", "health", "get", "--json", "--base-url", "http://127.0.0.1:9"])
    code = main(transport=_transport(refused))
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 3
    assert payload["ok"] is False
    assert "Could not reach" in payload["error"]
    assert captured.err == ""

    def missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Not Found"})

    monkeypatch.setattr(sys, "argv", ["anteroom", "api", "request", "GET", "/api/missing", "--json"])
    code = main(transport=_transport(missing))
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 1
    assert payload["ok"] is False
    assert payload["status"] == 404
    assert payload["error"] == "Not Found"
    assert captured.err == ""


def test_commands_json_lists_the_catalog(catalog_file, config_dir, capsys):
    _path, catalog = catalog_file
    del config_dir
    code = main(["commands", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["default_base_url"] == "http://127.0.0.1:4318"
    assert len(payload["commands"]) == len(catalog["commands"])
    assert payload["not_wrapped"][0]["service"] == "webhook-relay"
    assert "bearer token" in payload["auth"].lower()


def test_auth_stores_token_privately_and_whoami_uses_it(catalog_file, config_dir, capsys):
    del catalog_file

    code = main(["auth", "login", "--alias", "local", "--base-url", "http://127.0.0.1:4318", "--token", "secret-token", "--json"])
    assert code == 0
    saved = config_dir / "config.json"
    assert saved.is_file()
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert stat.S_IMODE(config_dir.stat().st_mode) == 0o700
    assert json.loads(saved.read_text())["environments"]["local"]["token"] == "secret-token"
    capsys.readouterr()

    code = main(["auth", "list"])
    listed = capsys.readouterr().out
    assert "secret-token" not in listed
    assert "token=yes" in listed

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret-token"
        return httpx.Response(200, json={"status": "ok", "version": "1"})

    code = main(["auth", "whoami", "--json"], transport=_transport(handler))
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["base_url"] == "http://127.0.0.1:4318"
    assert payload["token"] is True
    assert payload["data"]["status"] == "ok"

    assert main(["auth", "logout", "--alias", "local", "--json"]) == 4
    assert main(["auth", "logout", "--alias", "local", "--confirm", "--json"]) == 0
    assert main(["auth", "login", "--alias", "bad host", "--base-url", "http://127.0.0.1:9"]) == 2
    assert main(["auth", "login", "--alias", "remote", "--base-url", "http://user:pw@example.com"]) == 2


def test_stream_and_binary(catalog_file, config_dir, capsys, tmp_path: Path):
    del catalog_file, config_dir

    def stream(request: httpx.Request) -> httpx.Response:
        body = 'data: {"step": 1}\n\n: ping\n\ndata: [DONE]\n\n'
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())

    code = main(["jobs", "preview", "--job-id", "j1", "--json", "--stream-seconds", "5"], transport=_transport(stream))
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["data"]["events"][0] == {"step": 1}
    assert payload["data"]["events"][1] == {"done": True}
    assert payload["data"]["truncated"] is False

    picture = tmp_path / "shot.jpg"

    def image(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"\xff\xd8\xff\xd9")

    code = main(["--output", str(picture), "photos", "get", "--photo-id", "p1", "--json"], transport=_transport(image))
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert picture.read_bytes() == b"\xff\xd8\xff\xd9"
    assert payload["data"]["bytes"] == 4


def test_websocket_command_is_listed_and_refused(catalog_file, config_dir, capsys):
    del catalog_file, config_dir
    code = main(["extension", "ws", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert "WebSocket" in payload["error"]


def test_upload_and_version(catalog_file, config_dir, capsys, tmp_path: Path):
    del catalog_file, config_dir
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"jpeg")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/conversations/c1/photos"
        assert "multipart/form-data" in request.headers["content-type"]
        assert b"a.jpg" in request.content
        return httpx.Response(201, json={"count": 1})

    code = main(["photos", "conversations", "--conv-id", "c1", "--files", str(photo), "--json"], transport=_transport(handler))
    assert code == 0
    assert json.loads(capsys.readouterr().out)["data"]["count"] == 1

    code = main(["version", "--json"])
    assert json.loads(capsys.readouterr().out)["data"]["version"]


def test_mcp_lists_tools_and_calls_health(catalog_file, config_dir):
    del catalog_file, config_dir
    from vendoo_studio.cli.mcp_server import handle_message, serve

    init = handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}})
    assert init["result"]["serverInfo"]["name"] == "anteroom"
    assert init["result"]["protocolVersion"] == "2024-11-05"
    assert handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None

    listed = handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert "anteroom_commands" in names
    assert "anteroom_api_request" in names
    assert "health__get" in names
    assert "vendoo-api__conversations-list" in names
    publish = next(tool for tool in listed["result"]["tools"] if tool["name"] == "vendoo-api__conversations-list")
    assert "confirm" in publish["inputSchema"]["required"]

    refused = handle_message({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "boxes__delete", "arguments": {"box_id": "b1"}},
    })
    assert refused["result"]["isError"] is True

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/health"
        return httpx.Response(200, json={"status": "ok"})

    called = handle_message(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "health__get", "arguments": {}}},
        transport=_transport(handler),
    )
    assert called["result"]["isError"] is False
    body = json.loads(called["result"]["content"][0]["text"])
    assert body["data"]["status"] == "ok"

    raw = json.dumps({"version": "1.2.3", "entries": ["note " * 400]}).encode()

    def gzip_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json", "content-encoding": "gzip"},
            content=gzip.compress(raw),
        )

    via_api = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "anteroom_api_request", "arguments": {"method": "GET", "path": "/api/changelog"}},
        },
        transport=_transport(gzip_json),
    )
    assert via_api["result"]["isError"] is False
    assert json.loads(via_api["result"]["content"][0]["text"])["data"]["version"] == "1.2.3"

    stdin = StringIO(json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}) + "\n")
    stdout = StringIO()
    assert serve(stdin, stdout) == 0
    assert json.loads(stdout.getvalue())["result"] == {}
