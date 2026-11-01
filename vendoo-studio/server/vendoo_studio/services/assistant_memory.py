"""Facts the business assistant remembers across conversations.

[Mem0](https://github.com/mem0ai/mem0) is the memory layer people use for this:
read what you know, then add, update, or delete a fact when the conversation
changes it. Its open-source library stores vectors in Qdrant and phones home,
and its newer algorithm only adds. Studio keeps the shop on this machine, so
the same add / update / delete steps write SQLite rows instead. The assistant's
own reply carries the steps in one fenced block; nothing here calls another
service, and nothing here touches a listing.
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from vendoo_studio.models.assistant import AssistantMemory
from vendoo_studio.models.conversation import utcnow

# How many facts one shop holds, and how long one fact may be.
MAX_MEMORIES = 200
MAX_FACT_CHARS = 280
MEMORY_FENCE = "```memory"
# The changes block lives in the same reply. Both stay out of what the seller reads.
_FENCES = (MEMORY_FENCE, "```changes", "```automation")
_BLOCK = re.compile(r"```(memory|changes|automation)\s*(.*?)```", re.IGNORECASE | re.DOTALL)

INSTRUCTIONS = (
    "Remembering: you keep durable facts about how this seller runs the shop. They are listed in the snapshot "
    "under \"What you remember\", each with an id. They last after a conversation is cleared, so use them on "
    "every question, including a new chat.\n"
    "When this conversation establishes a fact that should still be true later, corrects one, or retires one, "
    "end the reply with one ```memory block. Put it after what you say to the seller, and after a ```changes "
    "block when you also have one:\n"
    "```memory\n"
    '[{"op": "add", "text": "Wants at least $15 profit per item."}, '
    '{"op": "update", "id": "FACT-ID", "text": "Target margin is 40%."}, '
    '{"op": "delete", "id": "FACT-ID"}]\n'
    "```\n"
    "- A fact is one sentence: how they source, what they will not sell, the margin or profit they want, "
    "where they ship from, how they price, a goal. Not a sale, a price, a listing, or anything the snapshot "
    "already records.\n"
    "- Update or delete a fact when the seller corrects it or takes it back. Do not add a second fact that "
    "says the opposite of one you already hold. Use an id from the list; do not invent one.\n"
    "- One block, and only when the list should change. Do not mention the block or quote it.\n"
)


def visible_text(reply: str) -> str:
    """What of a reply still being written the seller may read.

    A memory block and a changes block are held back, including a tail that
    might still grow into either fence.
    """
    lowered = reply.lower()
    at = len(reply)
    for fence in _FENCES:
        found = lowered.find(fence)
        if found >= 0:
            at = min(at, found)
    if at < len(reply):
        return reply[:at]
    hold = 0
    for fence in _FENCES:
        for size in range(min(len(fence) - 1, len(reply)), 0, -1):
            if fence.startswith(lowered[-size:]):
                hold = max(hold, size)
                break
    return reply[:-hold] if hold else reply


def peel(reply: str) -> tuple[str, str | None, str | None, str | None]:
    """A finished reply as words, the memory block, the changes block, and the automation block."""
    found: dict[str, str | None] = {"memory": None, "changes": None, "automation": None}

    def take(match: re.Match[str]) -> str:
        kind = match.group(1).lower()
        if found[kind] is None:
            found[kind] = match.group(2).strip()
        return ""

    words = _BLOCK.sub(take, reply)
    lowered = words.lower()
    cut = len(words)
    for fence, kind in ((MEMORY_FENCE, "memory"), ("```changes", "changes"), ("```automation", "automation")):
        at = lowered.find(fence)
        if at < 0:
            continue
        if found[kind] is None:
            found[kind] = words[at + len(fence):].strip()
        cut = min(cut, at)
    if cut < len(words):
        words = words[:cut]
    return words.rstrip(), found["memory"] or None, found["changes"] or None, found["automation"] or None


def section(db: Session) -> str:
    """The facts, oldest update first, for the snapshot the assistant reads."""
    rows = _rows(db)
    if not rows:
        return "--- What you remember ---\nNothing yet."
    lines = "\n".join(f"- {row.id}: {row.text}" for row in rows)
    return f"--- What you remember ---\n{lines}"


def list_memories(db: Session) -> list[dict[str, Any]]:
    """The facts for the seller, newest change first."""
    rows = _rows(db)
    return [view(row) for row in reversed(rows)]


def view(row: AssistantMemory) -> dict[str, Any]:
    updated = row.updated_at
    return {"id": row.id, "text": row.text, "updated_at": updated.isoformat() if updated else None}


def apply(db: Session, block: str) -> None:
    """Write the add, update, and delete steps in one memory block.

    A step that names an unknown id, repeats a fact already held, or arrives
    when the list is full is skipped. The caller commits.
    """
    rows = {row.id: row for row in db.query(AssistantMemory).all()}
    by_text = {_norm(row.text): row for row in rows.values()}
    for op in _operations(block):
        kind = str(op.get("op") or "").strip().lower()
        if kind in {"add", "update"}:
            text = _fact(op.get("text") or op.get("memory") or op.get("fact"))
        else:
            text = ""
        if kind == "add":
            if not text or _norm(text) in by_text or len(rows) >= MAX_MEMORIES:
                continue
            row = AssistantMemory(text=text)
            db.add(row)
            db.flush()
            rows[row.id] = row
            by_text[_norm(text)] = row
        elif kind == "update":
            row = rows.get(_memory_id(op))
            if row is None or not text:
                continue
            other = by_text.get(_norm(text))
            if other is not None and other.id != row.id:
                _drop(db, row, rows, by_text)
                continue
            by_text.pop(_norm(row.text), None)
            row.text = text
            row.updated_at = utcnow()
            by_text[_norm(text)] = row
        elif kind == "delete":
            row = rows.get(_memory_id(op))
            if row is not None:
                _drop(db, row, rows, by_text)


def forget(db: Session, memory_id: str) -> bool:
    """Drop one fact the seller says is wrong. Returns whether it was there."""
    row = db.get(AssistantMemory, memory_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def _rows(db: Session) -> list[AssistantMemory]:
    return (
        db.query(AssistantMemory)
        .order_by(AssistantMemory.updated_at, AssistantMemory.id)
        .all()
    )


def _operations(block: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(block)
    except ValueError:
        return []
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return []
    return [op for op in parsed if isinstance(op, dict)]


def _memory_id(op: dict[str, Any]) -> str:
    return str(op.get("id") or op.get("memory_id") or "").strip()


def _fact(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text[:MAX_FACT_CHARS].rstrip()


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def _drop(db: Session, row: AssistantMemory, rows: dict[str, AssistantMemory], by_text: dict[str, AssistantMemory]) -> None:
    rows.pop(row.id, None)
    by_text.pop(_norm(row.text), None)
    db.delete(row)
