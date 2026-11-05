"""Which copy of the seller's listings works on its own on this Mac.

Once a Mac signs in, its window shows either the seller's account or This
Mac's Listings, and remembers which. While it shows the account, the account
checks sales, delists after a sale and runs automations, promotions, eBay
offers and sourcing. This Mac's copy keeps every record and still does what
the seller asks of it, but starts none of that by itself, so nothing happens
twice. Showing This Mac's Listings again resumes it. A Mac that never showed
the account runs everything as before.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from vendoo_studio.config import DATA_DIR

WORKSPACE_FILE = Path(DATA_DIR) / "desktop-workspace.json"
WORKSPACES = ("account", "mac")


def saved() -> str | None:
    try:
        value = json.loads(WORKSPACE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    workspace = value.get("workspace") if isinstance(value, dict) else None
    return workspace if workspace in WORKSPACES else None


def save(workspace: str) -> None:
    """Replace the choice in one step, so the server never reads half of it."""
    WORKSPACE_FILE.parent.mkdir(parents=True, exist_ok=True)
    partial = WORKSPACE_FILE.with_name(WORKSPACE_FILE.name + ".tmp")
    partial.write_text(json.dumps({"workspace": workspace}), encoding="utf-8")
    os.replace(partial, WORKSPACE_FILE)


def account_shown() -> bool:
    """True while this Mac's window shows the seller's account. The hosted API never waits on it."""
    from vendoo_studio.services.service_context import current

    return current() is None and saved() == "account"
