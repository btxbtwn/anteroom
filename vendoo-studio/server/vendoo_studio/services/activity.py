"""Work Studio is still doing for a listing after (or beside) the chat stream.

Chat used to treat the end of its SSE stream as "done", but generation keeps
filling discovered fields afterwards, chat saves run as their own task, and
Vendoo syncs post to the conversation too. Everything that can still write to a
listing registers here so chat can show it and Stop can cancel it.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from dataclasses import dataclass


@dataclass(eq=False)
class Work:
    conv_id: str
    label: str
    task: asyncio.Task | None = None
    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True
        if self.task is not None and not self.task.done():
            self.task.cancel()


_work: dict[str, list[Work]] = {}
# The latest state of each ``live_trace.agent`` in a listing's current
# generation, by agent id. The generate stream carries these as ``agent``
# events, but the stream has ended by the time marketplace fields are filled,
# so activity polling exposes them too. Each generation gets a run token;
# events carrying an older token (a cancelled run still unwinding) are dropped.
_agents: dict[str, tuple[int, dict[str, dict]]] = {}
_runs = itertools.count(1)


def begin(conv_id: str, label: str) -> Work:
    """Register work the caller runs inline; call ``end`` when it stops."""
    work = Work(conv_id, label)
    _work.setdefault(conv_id, []).append(work)
    return work


def end(work: Work) -> None:
    items = _work.get(work.conv_id)
    if not items:
        return
    if work in items:
        items.remove(work)
    if not items:
        _work.pop(work.conv_id, None)


def track_task(conv_id: str, label: str, task: asyncio.Task) -> asyncio.Task:
    """Register a background task until it finishes; Stop cancels it."""
    work = begin(conv_id, label)
    work.task = task
    task.add_done_callback(lambda _task: end(work))
    return task


def running(conv_id: str) -> list[str]:
    labels = [work.label for work in _work.get(conv_id, []) if not work.cancelled]
    return list(dict.fromkeys(labels))


def running_all() -> dict[str, list[str]]:
    return {conv_id: labels for conv_id in _work if (labels := running(conv_id))}


def cancel(conv_id: str) -> int:
    items = list(_work.get(conv_id, []))
    for work in items:
        work.cancel()
    return len(items)


def begin_agents(conv_id: str) -> int:
    """Start a new generation's agent list; returns its run token."""
    token = next(_runs)
    _agents[conv_id] = (token, {})
    return token


def record_agent(conv_id: str, token: int, event: str | dict) -> None:
    """Keep the newest state of one agent (a ``live_trace`` ``agent`` event).

    Ignored unless ``token`` is the conversation's current run.
    """
    current = _agents.get(conv_id)
    if current is None or current[0] != token:
        return
    try:
        state = json.loads(event) if isinstance(event, str) else dict(event)
    except (TypeError, ValueError):
        return
    if not isinstance(state, dict) or not state.get("id"):
        return
    current[1][str(state["id"])] = state


def agents(conv_id: str) -> list[dict]:
    """Every agent of the listing's latest generation, in start order."""
    current = _agents.get(conv_id)
    states = current[1].values() if current else []
    return sorted(states, key=lambda state: state.get("started_at") or 0)


def clear_agents(conv_id: str) -> None:
    _agents.pop(conv_id, None)


def reset() -> None:
    for items in list(_work.values()):
        for work in items:
            work.cancel()
    _work.clear()
    _agents.clear()
