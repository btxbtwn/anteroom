"""Environments: which Anteroom base URL (and optional token) a command talks to.

The file is ``~/.config/anteroom/config.json`` unless ``ANTEROOM_CONFIG_DIR`` is set.
Tokens are written with mode 0600. ``anteroom auth list`` never prints a token.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any

from vendoo_studio.cli.constants import DEFAULT_BASE_URL
from vendoo_studio.cli.errors import UsageError


def config_dir() -> Path:
    override = os.environ.get("ANTEROOM_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "anteroom"


def config_path() -> Path:
    return config_dir() / "config.json"


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.is_file():
        return {"current": "local", "environments": {"local": {"base_url": DEFAULT_BASE_URL}}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise UsageError(f"Could not read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise UsageError(f"Could not read {path}: expected an object")
    data.setdefault("current", "local")
    data.setdefault("environments", {})
    return data


def save_config(data: dict[str, Any]) -> None:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    path = config_path()
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)


def validate_base_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise UsageError("Base URL must start with http:// or https:// and include a host")
    if parsed.username or parsed.password:
        raise UsageError("Put a token in --token. Do not embed credentials in the URL")
    if parsed.query or parsed.fragment:
        raise UsageError("Base URL must not include a query string or fragment")
    return url.strip().rstrip("/")


def base_url_warning(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    loopback = host in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme == "http" and not loopback:
        return (
            f"{url} is not a loopback address and uses plain HTTP. "
            "Anteroom does not authenticate API calls; any machine that can reach this URL can call it."
        )
    return None


def public_environments(data: dict[str, Any]) -> list[dict[str, Any]]:
    current = data.get("current") or "local"
    rows = []
    for alias, entry in sorted((data.get("environments") or {}).items()):
        if not isinstance(entry, dict):
            continue
        rows.append({
            "alias": alias,
            "base_url": entry.get("base_url") or "",
            "token": bool(entry.get("token")),
            "current": alias == current,
        })
    return rows


class Target:
    def __init__(self, alias: str, base_url: str, token: str | None):
        self.alias = alias
        self.base_url = base_url
        self.token = token or None


def resolve_target(*, environment: str | None, base_url: str | None) -> Target:
    """Pick the server for this invocation.

    Precedence: ``--base-url``, ``ANTEROOM_BASE_URL``, the named environment,
    then the current environment, then the local default.
    """
    data = load_config()
    environments = data.get("environments") or {}
    alias = environment or os.environ.get("ANTEROOM_ENV") or data.get("current") or "local"
    selected = environments.get(alias) if isinstance(environments.get(alias), dict) else None
    if environment and selected is None and not base_url and not os.environ.get("ANTEROOM_BASE_URL"):
        known = ", ".join(sorted(environments)) or "(none saved)"
        raise UsageError(f"Unknown environment {environment!r}. Saved environments: {known}")
    override = base_url or os.environ.get("ANTEROOM_BASE_URL")
    if override:
        resolved = validate_base_url(override)
    elif selected and selected.get("base_url"):
        resolved = validate_base_url(str(selected["base_url"]))
    else:
        resolved = DEFAULT_BASE_URL
    token = os.environ.get("ANTEROOM_TOKEN")
    if token is None and selected:
        stored = selected.get("token")
        token = str(stored) if stored else None
    if token is not None and token.strip() == "":
        token = None
    return Target(alias if not override else alias, resolved, token)


def login(alias: str, base_url: str, token: str | None, *, clear_token: bool) -> dict[str, Any]:
    if not alias or not re_alias(alias):
        raise UsageError("Alias must be letters, numbers, dots, or dashes (for example local)")
    data = load_config()
    environments = data.setdefault("environments", {})
    previous = environments.get(alias) if isinstance(environments.get(alias), dict) else {}
    entry: dict[str, Any] = {"base_url": validate_base_url(base_url)}
    if token:
        entry["token"] = token
    elif not clear_token and previous.get("token"):
        entry["token"] = previous["token"]
    environments[alias] = entry
    data["current"] = alias
    save_config(data)
    return data


def logout(alias: str) -> dict[str, Any]:
    data = load_config()
    environments = data.get("environments") or {}
    if alias not in environments:
        raise UsageError(f"Unknown environment {alias!r}")
    environments.pop(alias, None)
    if data.get("current") == alias:
        data["current"] = next(iter(environments), "local")
        if data["current"] not in environments:
            environments["local"] = {"base_url": DEFAULT_BASE_URL}
    save_config(data)
    return data


def use(alias: str) -> dict[str, Any]:
    data = load_config()
    if alias not in (data.get("environments") or {}):
        raise UsageError(f"Unknown environment {alias!r}. Run `anteroom auth login` first.")
    data["current"] = alias
    save_config(data)
    return data


def re_alias(alias: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", alias))
