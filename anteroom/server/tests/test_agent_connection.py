"""Settings → Agents: CLI discovery, copy-ready setup, and a health-only connection test."""

from __future__ import annotations

import json
import os
import stat

from fastapi.testclient import TestClient

from anteroom.cli.catalog import is_destructive
from anteroom.main import app


def _script(path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_agent_status_describes_a_found_command(monkeypatch, tmp_path):
    binary = tmp_path / "anteroom"
    _script(
        binary,
        "#!/bin/sh\nprintf '%s\\n' '{\"ok\":true,\"data\":{\"version\":\"9.9.9\"}}'\n",
    )
    monkeypatch.setattr("anteroom.services.agent_connection.find_cli", lambda: str(binary))
    monkeypatch.setattr("anteroom.services.agent_connection.shutil.which", lambda name: str(binary) if name == "anteroom" else None)
    monkeypatch.setattr("anteroom.services.agent_connection.remote_base_url", lambda: "https://mac.tail.ts.net:9444")

    body = TestClient(app).get("/api/settings/agent").json()

    assert body["installed"] is True
    assert body["on_path"] is True
    assert body["version"] == "9.9.9"
    assert body["base_url"].startswith("http://127.0.0.1:")
    assert body["login_command"] == f"{binary} auth login --alias local --base-url {body['base_url']}"
    assert body["remote_base_url"] == "https://mac.tail.ts.net:9444"
    assert "studio" in body["remote_login_command"]
    assert body["install_command"] is None
    ids = [client["id"] for client in body["clients"]]
    assert ids == ["cursor", "claude-desktop", "claude-code"]
    for client in body["clients"]:
        assert str(binary) in client["snippet"]
        assert '"mcp"' in client["snippet"]
        assert '"serve"' in client["snippet"]
        assert client["label"] in client["prompt"]
        assert client["snippet"] in client["prompt"]
        assert body["base_url"] in client["prompt"]
        assert "stdio" in client["prompt"]
    claude = next(client for client in body["clients"] if client["id"] == "claude-code")
    assert claude["command"].startswith("claude mcp add --transport stdio anteroom -- ")
    assert "mcp serve" in claude["command"]
    assert claude["command"] in claude["prompt"]
    assert is_destructive("GET", "/api/settings/agent", set()) is None
    assert is_destructive("POST", "/api/settings/agent/test", set()) is None


def test_agent_status_when_the_command_is_missing(monkeypatch):
    monkeypatch.setattr("anteroom.services.agent_connection.find_cli", lambda: None)
    monkeypatch.setattr("anteroom.services.agent_connection.shutil.which", lambda name: None)
    monkeypatch.setattr("anteroom.services.agent_connection.remote_base_url", lambda: None)

    body = TestClient(app).get("/api/settings/agent").json()

    assert body["installed"] is False
    assert body["on_path"] is False
    assert body["version"] is None
    assert body["install_command"] == "pip install -e ."
    assert "virtualenv" in body["hint"]
    assert "anteroom" in body["clients"][0]["snippet"]


def test_agent_connection_test_calls_health_and_stops_when_missing(monkeypatch, tmp_path):
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["timeout"] = kwargs.get("timeout")

        class Completed:
            returncode = 0
            stdout = '{"ok":true,"status":200,"data":{"status":"ok","version":"9.9.9","service":"anteroom"}}'
            stderr = ""

        return Completed()

    binary = tmp_path / "anteroom"
    _script(binary, "#!/bin/sh\nexit 0\n")
    monkeypatch.setattr("anteroom.services.agent_connection.find_cli", lambda: str(binary))
    monkeypatch.setattr("anteroom.services.agent_connection.subprocess.run", fake_run)

    ok = TestClient(app).post("/api/settings/agent/test").json()
    assert ok["ok"] is True
    assert "9.9.9" in ok["detail"]
    assert seen["args"][1:5] == ["health", "get", "--json", "--base-url"]
    assert seen["args"][5] == ok["base_url"]
    assert seen["args"][0] == str(binary)

    monkeypatch.setattr("anteroom.services.agent_connection.find_cli", lambda: None)
    missing = TestClient(app).post("/api/settings/agent/test").json()
    assert missing["ok"] is False
    assert "not installed" in missing["detail"]


def test_agent_connection_test_reports_a_failed_command(monkeypatch, tmp_path):
    binary = tmp_path / "anteroom"
    _script(
        binary,
        "#!/bin/sh\nprintf '%s\\n' '{\"ok\":false,\"error\":\"Could not reach http://127.0.0.1:4318\"}' >&1\nexit 3\n",
    )
    monkeypatch.setattr("anteroom.services.agent_connection.find_cli", lambda: str(binary))
    # The script is not the app's own interpreter, so version probing is not involved here.
    body = TestClient(app).post("/api/settings/agent/test").json()
    assert body["ok"] is False
    assert "Could not reach" in body["detail"]
    assert os.access(binary, os.X_OK)


def test_http_setup_prompts_use_the_served_url_and_authentication():
    from anteroom.services.agent_connection import _remote_clients

    for url, require_token in [("http://127.0.0.1:4319/api/mcp", False), ("https://example.com/api/mcp", True)]:
        clients = {client["id"]: client for client in _remote_clients(url, require_token=require_token)}
        assert set(clients) == {"claude", "chatgpt", "antigravity", "generic"}
        for client in clients.values():
            assert url in client["prompt"]
            assert "Streamable HTTP" in client["prompt"]
            assert client["snippet"] in client["prompt"]
            assert ("Create token" in client["prompt"]) is require_token
        antigravity = json.loads(clients["antigravity"]["snippet"])["mcpServers"]["anteroom"]
        assert antigravity == {
            "serverUrl": url,
            **({"headers": {"Authorization": "Bearer arm_your_access_token"}} if require_token else {}),
        }
        assert clients["antigravity"]["command"] is None
        assert ("bearer_token_env_var" in clients["chatgpt"]["snippet"]) is require_token
        assert ("Authorization: Bearer arm_your_access_token" in clients["generic"]["snippet"]) is require_token
