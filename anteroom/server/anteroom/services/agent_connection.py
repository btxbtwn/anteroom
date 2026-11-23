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
    from anteroom.config import HOST, PORT

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
        from anteroom.services.tailscale_serve import status
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
    mcp_base = remote or base_url
    mcp_url = f"{mcp_base.rstrip('/')}/api/mcp"
    return {
        "hosted": False,
        "installed": path is not None,
        "on_path": on_path,
        "path": path,
        "version": version,
        "version_error": version_error,
        "base_url": base_url,
        "remote_base_url": remote,
        "mcp_url": mcp_url,
        "login_command": f"{command} auth login --alias local --base-url {base_url}",
        "remote_login_command": (
            f"{command} auth login --alias studio --base-url {remote}" if remote else None
        ),
        "install_command": None if path else _INSTALL_COMMAND,
        "hint": _hint(installed=path is not None, on_path=on_path),
        "clients": _clients(path or "anteroom", base_url),
        "remote_clients": _remote_clients(mcp_url, require_token=bool(remote)),
    }


def describe_hosted(base_url: str) -> dict[str, Any]:
    """Remote MCP connection details for hosted myanteroom.com sellers."""
    origin = (base_url or "https://myanteroom.com").rstrip("/")
    mcp_url = f"{origin}/api/mcp"
    return {
        "hosted": True,
        "installed": False,
        "on_path": False,
        "path": None,
        "version": _app_version() or None,
        "version_error": None,
        "base_url": origin,
        "remote_base_url": origin,
        "mcp_url": mcp_url,
        "login_command": "",
        "remote_login_command": None,
        "install_command": None,
        "hint": None,
        "clients": [],
        "remote_clients": _remote_clients(mcp_url, require_token=True),
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
    if data.get("service") not in {None, "anteroom", "vendoo-studio"}:
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
        return "From the anteroom folder, with its virtualenv active, run the install command. Then reopen this page."
    if not on_path:
        return "This copy is not on your PATH. The setup below uses its full path, so Cursor and Claude can launch it."
    return None


def _with_setup_prompts(
    clients: list[dict[str, Any]], transport: str, guidance: str
) -> list[dict[str, Any]]:
    for client in clients:
        parts = [
            f"Set up my Anteroom MCP server using {transport}.",
            f"{client['label']} setup\n{client['where']}",
            guidance,
        ]
        if client["command"]:
            parts.append(f"Terminal option\n```sh\n{client['command']}\n```")
        parts.extend([
            f"Configuration\n```\n{client['snippet']}\n```",
            "Preserve my other MCP servers and settings. If you can't change the settings yourself, "
            "walk me through the steps I need to take. Verify the connection by listing the Anteroom tools "
            "and tell me what worked or what blocked setup. Keep my listing data unchanged during setup.",
        ])
        client["prompt"] = "\n\n".join(parts)
    return clients


def _clients(command: str, base_url: str) -> list[dict[str, Any]]:
    snippet = json.dumps(
        {"mcpServers": {"anteroom": {"command": command, "args": ["mcp", "serve"]}}},
        indent=2,
    )
    quoted = shlex.quote(command)
    clients = [
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
    return _with_setup_prompts(
        clients,
        "local stdio",
        f"Run the server on the Mac running Anteroom. Use the executable in the configuration below. "
        f"Set ANTEROOM_BASE_URL to {base_url} in the MCP server's environment to target this app. "
        "If the anteroom command is missing, help me install it from Anteroom's Agents settings first.",
    )


def _remote_clients(mcp_url: str, *, require_token: bool) -> list[dict[str, Any]]:
    token_placeholder = "arm_your_access_token"
    headers_block = {"Authorization": f"Bearer {token_placeholder}"} if require_token else {}

    claude_entry: dict[str, Any] = {"type": "http", "url": mcp_url}
    if headers_block:
        claude_entry["headers"] = headers_block
    claude_snippet = json.dumps({"mcpServers": {"anteroom": claude_entry}}, indent=2)
    claude_cmd = (
        f"claude mcp add --transport http anteroom {shlex.quote(mcp_url)} "
        f'--header "Authorization: Bearer {token_placeholder}"'
        if require_token
        else f"claude mcp add --transport http anteroom {shlex.quote(mcp_url)}"
    )

    codex_snippet = f"[mcp_servers.anteroom]\nurl = {json.dumps(mcp_url)}"
    if require_token:
        codex_snippet += '\nbearer_token_env_var = "ANTEROOM_MCP_TOKEN"'

    antigravity_entry: dict[str, Any] = {"serverUrl": mcp_url}
    if headers_block:
        antigravity_entry["headers"] = headers_block
    antigravity_snippet = json.dumps({"mcpServers": {"anteroom": antigravity_entry}}, indent=2)
    generic_snippet = f"Name: anteroom\nTransport: Streamable HTTP\nServer URL: {mcp_url}"
    if require_token:
        generic_snippet += f"\nAuthorization: Bearer {token_placeholder}"

    clients = [
        {
            "id": "claude",
            "label": "Claude",
            "where": (
                "Use Claude Code, or add a personal custom connector in Claude for a public HTTPS endpoint. "
                "In the connector, enter the URL and add Authorization under Request headers when required. "
                "Use Claude Code for a local or private-network endpoint."
            ),
            "snippet": claude_snippet,
            "command": claude_cmd,
        },
        {
            "id": "chatgpt",
            "label": "Codex / ChatGPT",
            "where": (
                "Merge this into the Codex config.toml. For an authenticated endpoint, set ANTEROOM_MCP_TOKEN "
                "in the environment that launches Codex. ChatGPT’s connector settings require OAuth for "
                "authenticated servers, so use Codex for this token-based setup."
            ),
            "snippet": codex_snippet,
            "command": None,
        },
        {
            "id": "antigravity",
            "label": "Google Antigravity",
            "where": (
                "In Antigravity IDE, open MCP Servers → Manage MCP Servers → View raw config. "
                "Merge the anteroom entry into mcpServers in mcp_config.json."
            ),
            "snippet": antigravity_snippet,
            "command": None,
        },
        {
            "id": "generic",
            "label": "Other MCP clients",
            "where": "Use your client's setup format for a Streamable HTTP MCP server.",
            "snippet": generic_snippet,
            "command": None,
        },
    ]
    guidance = (
        "In Anteroom → Settings → Agents → Access tokens, use Create token. "
        f"I’ll enter the token in the client's private settings or secret store where {token_placeholder} "
        "or a token environment variable is referenced. Keep the token out of chat, logs, and shared configuration. "
        "If the client cannot reach the URL or send the bearer token, explain what is missing."
        if require_token
        else "This loopback endpoint is for a client running on the same Mac as Anteroom. "
        "It needs no access token. If your client runs elsewhere, tell me it needs a reachable endpoint."
    )
    return _with_setup_prompts(clients, "Streamable HTTP", guidance)


def _parse_stdout(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None
