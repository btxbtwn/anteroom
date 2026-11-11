"""Private Tailscale Serve for Studio.

Keeps FastAPI on loopback. Tailscale terminates HTTPS on the tailnet and proxies to
http://127.0.0.1:4318. Funnel is never enabled.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any

from vendoo_studio.config import HOST, PORT

# Already used for Studio on this machine's tailnet.
DEFAULT_HTTPS_PORT = 9444
HTTPS_PORT = int(os.environ.get("VENDOO_STUDIO_TAILSCALE_HTTPS_PORT", str(DEFAULT_HTTPS_PORT)))
TARGET = f"http://{HOST}:{PORT}"
_TIMEOUT_S = 15


class TailscaleServeError(RuntimeError):
    """User-facing failure configuring Tailscale Serve."""


def _which_tailscale() -> str | None:
    return shutil.which("tailscale")


def _run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    binary = _which_tailscale()
    if not binary:
        raise TailscaleServeError(
            "Tailscale is not installed (or not on PATH). Install Tailscale, then try again."
        )
    try:
        return subprocess.run(
            [binary, *args],
            check=check,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise TailscaleServeError("Tailscale did not respond in time.") from exc
    except FileNotFoundError as exc:
        raise TailscaleServeError(
            "Tailscale is not installed (or not on PATH). Install Tailscale, then try again."
        ) from exc


def _run_json(args: list[str]) -> Any:
    completed = _run(args, check=False)
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        raise TailscaleServeError(err or f"tailscale {' '.join(args)} failed")
    raw = (completed.stdout or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TailscaleServeError("Tailscale returned invalid JSON.") from exc


def identity() -> tuple[str | None, str | None]:
    """This Mac's MagicDNS name, and the Tailscale login of the person who owns it.

    Only that login may use Anteroom over Serve. A tagged Mac belongs to no
    person, so it has no owner login and Serve admits nobody.
    """
    status = _run_json(["status", "--json"])
    self_node = status.get("Self") if isinstance(status, dict) else None
    if not isinstance(self_node, dict):
        return None, None
    name = str(self_node.get("DNSName") or "").rstrip(".") or None
    users = status.get("User") if isinstance(status, dict) else None
    user = users.get(str(self_node.get("UserID"))) if isinstance(users, dict) else None
    login = str(user.get("LoginName") or "") if isinstance(user, dict) else ""
    if self_node.get("Tags") or not login:
        return name, None
    return name, login


def _serve_status() -> dict:
    raw = _run_json(["serve", "status", "--json"])
    return raw if isinstance(raw, dict) else {}


def _route_key(host: str) -> str:
    return f"{host}:{HTTPS_PORT}"


def _expected_web_handler() -> dict:
    return {"Handlers": {"/": {"Proxy": TARGET}}}


def _classify_route(config: dict, host: str) -> str:
    """Return free | configured | conflict | funnel."""
    port_key = str(HTTPS_PORT)
    route = _route_key(host)
    allow_funnel = config.get("AllowFunnel") or {}
    if isinstance(allow_funnel, dict) and bool(allow_funnel.get(route)):
        return "funnel"

    tcp = (config.get("TCP") or {}).get(port_key)
    web = (config.get("Web") or {}).get(route)

    if tcp is None and web is None:
        return "free"
    if tcp == {"HTTPS": True} and web == _expected_web_handler():
        return "configured"
    return "conflict"


def _public_url(host: str) -> str:
    return f"https://{host}:{HTTPS_PORT}"


def status() -> dict[str, Any]:
    """Inspect Tailscale and whether Studio's private Serve route is active."""
    from vendoo_studio.services.local_requests import set_private_origin

    payload, owner = _status()
    set_private_origin(payload["url"] if payload["enabled"] else None, owner)
    return payload


def _status() -> tuple[dict[str, Any], str | None]:
    payload: dict[str, Any] = {
        "available": False,
        "installed": bool(_which_tailscale()),
        "enabled": False,
        "https_port": HTTPS_PORT,
        "target": TARGET,
        "dns_name": None,
        "url": None,
        "state": "missing",
        "error": None,
        "funnel": False,
    }
    if not payload["installed"]:
        payload["error"] = "Tailscale is not installed (or not on PATH)."
        return payload, None

    owner = None
    try:
        host, owner = identity()
        if not host:
            payload["state"] = "logged_out"
            payload["error"] = "Tailscale is installed but this machine has no MagicDNS name. Sign in to Tailscale."
            return payload, None
        payload["dns_name"] = host
        payload["url"] = _public_url(host)
        payload["available"] = True

        config = _serve_status()
        state = _classify_route(config, host)
        payload["state"] = state
        payload["funnel"] = state == "funnel"
        payload["enabled"] = state == "configured"
        if state == "funnel":
            payload["error"] = (
                f"Tailscale Funnel is on for port {HTTPS_PORT}. Turn Funnel off for that route, then enable again."
            )
        elif state == "conflict":
            payload["error"] = (
                f"HTTPS port {HTTPS_PORT} is already used by another Tailscale Serve route. "
                f"Run `tailscale serve status` or set VENDOO_STUDIO_TAILSCALE_HTTPS_PORT to a free port."
            )
        elif state == "configured" and not owner:
            payload["error"] = (
                "This Mac is a tagged Tailscale device, so it has no owner to let in. "
                "Anteroom only answers its owner's Tailscale account; sign this Mac in as yourself."
            )
    except TailscaleServeError as exc:
        payload["error"] = str(exc)
        payload["state"] = "error"
    return payload, owner


def enable() -> dict[str, Any]:
    current = status()
    if current.get("enabled"):
        return current
    if not current.get("installed"):
        raise TailscaleServeError(current.get("error") or "Tailscale is not installed.")
    host = current.get("dns_name")
    if not host:
        raise TailscaleServeError(current.get("error") or "Tailscale is not signed in.")
    state = current.get("state")
    if state == "funnel":
        raise TailscaleServeError(current["error"] or "Funnel is enabled for this route.")
    if state == "conflict":
        raise TailscaleServeError(current["error"] or "Serve port conflict.")

    completed = _run(["serve", "--bg", f"--https={HTTPS_PORT}", TARGET], check=False)
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        raise TailscaleServeError(err or "Failed to enable Tailscale Serve.")

    updated = status()
    if not updated.get("enabled"):
        raise TailscaleServeError(
            updated.get("error")
            or "Tailscale reported success but Anteroom's Serve route was not present."
        )
    return updated


def disable() -> dict[str, Any]:
    current = status()
    if not current.get("installed"):
        raise TailscaleServeError(current.get("error") or "Tailscale is not installed.")

    host = current.get("dns_name")
    if host:
        config = _serve_status()
        state = _classify_route(config, host)
        if state == "free":
            current["enabled"] = False
            current["state"] = "free"
            current["error"] = None
            return current
        if state == "conflict":
            raise TailscaleServeError(
                f"Refusing to clear HTTPS port {HTTPS_PORT}: it is not Anteroom's Serve route."
            )
        if state == "funnel":
            raise TailscaleServeError(
                "Funnel is enabled for this route. Disable Funnel in Tailscale before turning off Anteroom's Serve route."
            )

    completed = _run(["serve", f"--https={HTTPS_PORT}", "off"], check=False)
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        raise TailscaleServeError(err or "Failed to disable Tailscale Serve.")

    updated = status()
    if updated.get("enabled"):
        raise TailscaleServeError("Tailscale Serve is still enabled after disable.")
    return updated
