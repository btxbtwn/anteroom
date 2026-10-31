"""How an agent on this machine connects with the anteroom command.

The settings page and ``anteroom settings agent`` both read this. Finding the
command does not publish, delete, or change a saved environment. The connection
test only calls this app's health route.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

_TIMEOUT_S = 30
_INSTALL_COMMAND = "pip install -e ."


def loopback_base_url() -> str:
    from vendoo_studio.config import HOST, PORT

    host = HOST if HOST not in {"0.0.0.0", "::", ""} else "127.0.0.1"
    return f"http://{host}:{PORT}"


def find_cli() -> str | None:
    """The anteroom executable, preferring one already on PATH."""
    found = shutil.which("anteroom")
    if found:
        return found
    sibling = Path(sys.executable).resolve().parent / "anteroom"
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return str(sibling)
    return None


def remote_base_url() -> str | None:
    """The Tailscale HTTPS address, when Settings → Connections has it on."""
    try:
        from vendoo_studio.services.tailscale_serve import status
    except Exception:
        return None
    try:
        payload = status()
    except Exception:
        return None
    if payload.get("enabled") and payload.get("url"):
        return str(payload["url"])
    return None


def describe() -> dict[str, Any]:
    path = find_cli()
    on_path = bool(path) and shutil.which("anteroom") is not None
    version = None
    version_error = None
    if path:
        version, version_error = read_version(path)
    command = _display_command(path)
    base_url = loopback_base_url()
    remote = remote_base_url()
    return {
        "installed": path is not None,
        "on_path": on_path,
        "path": path,
        "version": version,
        "version_error": version_error,
        "base_url": base_url,
        "remote_base_url": remote,
        "login_command": f"{command} auth login --alias local --base-url {base_url}",
        "remote_login_command": (
            f"{command} auth login --alias studio --base-url {remote}" if remote else None
        ),
        "install_command": None if path else _INSTALL_COMMAND,
        "hint": _hint(installed=path is not None, on_path=on_path),
        "clients": _clients(path or "anteroom"),
    }


def test_connection() -> dict[str, Any]:
    path = find_cli()
    base_url = loopback_base_url()
    if not path:
        return {
            "ok": False,
            "detail": "The anteroom command is not installed.",
            "version": None,
            "base_url": base_url,
        }
    try:
        completed = subprocess.run(
            [path, "health", "get", "--json", "--base-url", base_url],
            check=False,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "detail": "The anteroom command did not answer in time.",
            "version": None,
            "base_url": base_url,
        }
    payload = _parse_stdout(completed.stdout)
    if completed.returncode != 0 or not payload or payload.get("ok") is not True:
        detail = ""
        if isinstance(payload, dict) and isinstance(payload.get("error"), str):
            detail = payload["error"]
        if not detail:
            detail = (completed.stderr or completed.stdout or "").strip()
        if not detail:
            detail = f"The anteroom command exited with code {completed.returncode}."
        else:
            detail = f"The anteroom command could not reach this app ({detail})."
        return {"ok": False, "detail": detail[:500], "version": None, "base_url": base_url}
    data = payload.get("data")
    if not isinstance(data, dict):
        data = {}
    if data.get("service") not in {None, "vendoo-studio"}:
        return {
            "ok": False,
            "detail": "The command reached a different service.",
            "version": None,
            "base_url": base_url,
        }
    version = data.get("version")
    shown = f" ({version})" if isinstance(version, str) and version else ""
    return {
        "ok": True,
        "detail": f"The anteroom command reached this app{shown}.",
        "version": version if isinstance(version, str) else None,
        "base_url": base_url,
    }


def read_version(path: str) -> tuple[str | None, str | None]:
    """Version of this command. The copy next to the app needs no subprocess."""
    if _same_install(path):
        text = _app_version()
        return (text or None), None
    try:
        completed = subprocess.run(
            [path, "version", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return None, "anteroom version did not finish in time."
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return None, (detail or "anteroom version failed.")[:300]
    payload = _parse_stdout(completed.stdout)
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict) and isinstance(data.get("version"), str) and data["version"]:
        return data["version"], None
    text = (completed.stdout or "").strip()
    if text and "\n" not in text and len(text) < 40:
        return text, None
    return None, "anteroom version did not return a version."


def _same_install(path: str) -> bool:
    sibling = Path(sys.executable).resolve().parent / "anteroom"
    try:
        return Path(path).resolve().samefile(sibling)
    except OSError:
        return False


def _app_version() -> str:
    version_file = Path(__file__).resolve().parents[3] / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _display_command(path: str | None) -> str:
    if not path:
        return "anteroom"
    return shlex.quote(path)


def _hint(*, installed: bool, on_path: bool) -> str | None:
    if not installed:
        return "From the vendoo-studio folder, with its virtualenv active, run the install command. Then reopen this page."
    if not on_path:
        return "This copy is not on your PATH. The setup below uses its full path, so Cursor and Claude can launch it."
    return None


def _clients(command: str) -> list[dict[str, Any]]:
    snippet = json.dumps(
        {"mcpServers": {"anteroom": {"command": command, "args": ["mcp", "serve"]}}},
        indent=2,
    )
    quoted = shlex.quote(command)
    return [
        {
            "id": "cursor",
            "label": "Cursor",
            "where": "Cursor → Settings → MCP → New MCP server, or merge this into ~/.cursor/mcp.json.",
            "snippet": snippet,
            "command": None,
        },
        {
            "id": "claude-desktop",
            "label": "Claude Desktop",
            "where": (
                "Claude Desktop → Settings → Developer → Edit Config "
                "(~/Library/Application Support/Claude/claude_desktop_config.json). "
                "Merge the anteroom entry into mcpServers if that file already has other servers."
            ),
            "snippet": snippet,
            "command": None,
        },
        {
            "id": "claude-code",
            "label": "Claude Code",
            "where": "Run the command in a terminal, or merge the JSON into the project's .mcp.json.",
            "snippet": snippet,
            "command": f"claude mcp add --transport stdio anteroom -- {quoted} mcp serve",
        },
    ]


def _parse_stdout(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None
