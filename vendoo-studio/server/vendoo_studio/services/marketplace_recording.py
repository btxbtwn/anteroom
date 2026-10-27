"""One-time recordings of a marketplace's own requests while the seller acts by hand.

A recording runs as a task inside Anteroom and writes its result to the data
folder, so it survives the caller disconnecting while the seller is still in
the tab. Results hold request shapes only, never values or session data.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from vendoo_studio.config import DATA_DIR
from vendoo_studio.services import browser_bridge

RECORDABLE = {"draft": {"mercari", "sellwild", "facebook"}, "offers": {"mercari", "depop"}}
RECORDINGS_DIR = Path(DATA_DIR) / "recordings"
_tasks: dict[tuple[str, str], asyncio.Task] = {}


def _path(marketplace: str, goal: str) -> Path:
    return RECORDINGS_DIR / f"{marketplace}-{goal}.json"


async def _run(marketplace: str, goal: str) -> None:
    started_at = datetime.now(UTC).isoformat()
    try:
        result = await browser_bridge.request(
            SimpleNamespace(id=None), "marketplace.record", {"marketplace": marketplace, "goal": goal}, timeout=330,
        )
    except browser_bridge.BrowserBridgeError as exc:
        result = {"ok": False, "error": str(exc)}
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    saved = {**(result if isinstance(result, dict) else {"ok": False, "error": "Chrome sent no recording."}),
             "started_at": started_at, "finished_at": datetime.now(UTC).isoformat()}
    _path(marketplace, goal).write_text(json.dumps(saved, indent=1))


def running(marketplace: str, goal: str) -> bool:
    task = _tasks.get((marketplace, goal))
    return bool(task and not task.done())


def start(marketplace: str, goal: str) -> bool:
    """Start a recording unless one is already running. Returns whether one started."""
    if any(not task.done() for task in _tasks.values()):
        return False
    _tasks[(marketplace, goal)] = asyncio.create_task(_run(marketplace, goal))
    return True


def status(marketplace: str, goal: str) -> dict:
    path = _path(marketplace, goal)
    result = json.loads(path.read_text()) if path.exists() else None
    return {"recording": running(marketplace, goal), "result": result}
