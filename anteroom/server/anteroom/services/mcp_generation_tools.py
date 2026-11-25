"""``generate_listings``: Generate for drafts an AI assistant names, as Bulk upload's "Create and generate".

Generation writes the listing from the draft's photos and Item Details and stops
at a saved draft: it never publishes or reaches a marketplace. Only drafts are
generated; an item live on a marketplace or sold is skipped, so nothing buyers
see is rewritten.

On the hosted app each draft is queued as the seller's durable ``generate``
draft task, as the editor's Generate and automations do; a worker runs it
whether or not anyone is watching. On the Mac the handler only checks the
drafts inside the call's database session; the route starts each generation
once that session is closed (``routes/mcp.py``), and the run keeps going after
the call answers.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from anteroom.models.marketplace_listing import LIVE_STATES, MarketplaceListing
from anteroom.repositories.queries import ConversationRepo
from anteroom.services.service_context import current

_PLACEHOLDER = "_anteroom_generation"
# The hosted app's per-seller budget for new background work, set by the route:
# each draft queued here takes from it, as each Generate in the app does.
submission_limit: ContextVar[Callable[[], None] | None] = ContextVar("mcp_submission_limit", default=None)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "generate_listings",
        "title": "Generate drafts",
        "description": (
            "Start generating the listing for each named draft from its photos and Item Details, like "
            "Generate in Anteroom or Bulk upload's Create and generate. Item Details (size, measurements, SKU, "
            "labels, notes) are used as the seller's facts, so set them first (read_size_tag, read_measurements, "
            "edit_listing sku, manage_labels_and_notes). Only drafts with photos are generated; a draft "
            "already generating, live or sold is skipped with the reason. Returns once each one has started; "
            "follow each with get_listing's generation field (queued, running, completed or failed with the "
            "reason). Generation "
            "stops at a saved draft: it never publishes or reaches a marketplace."
        ),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "uniqueItems": True,
                    "description": "The drafts to generate (listing / conversation IDs).",
                },
            },
            "required": ["conversation_ids"],
            "additionalProperties": False,
        },
    },
]

# Generation rewrites the draft, so it needs Listings write access.
PERMISSIONS: dict[str, tuple[str, str]] = {"generate_listings": ("listings", "write")}

_NOTE = (
    "Generating in Anteroom. Each draft is saved when it finishes, and nothing is published. "
    "Follow each with get_listing: its generation field says queued, running, completed or failed."
)


class GenerationToolError(ValueError):
    """A refusal the agent reads, as an MCP tool error."""


def _conversation_ids(args: dict[str, Any]) -> list[str]:
    raw = args.get("conversation_ids")
    if not isinstance(raw, list) or not raw:
        raise GenerationToolError("conversation_ids must list at least one draft.")
    ids: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            raise GenerationToolError("Each conversation_id must be a listing ID.")
        if value.strip() not in ids:
            ids.append(value.strip())
    return ids


def _why_not(db: Session, conv_id: str) -> str | None:
    """Why this item is not generated, or None when it is a draft with photos."""
    repo = ConversationRepo(db)
    conv = repo.get(conv_id)
    if conv is None:
        return "Listing not found."
    if conv.status == "in_progress":
        return "Already generating."
    if conv.status != "draft":
        return f"Not a draft (it is {conv.status}): generating would rewrite a listing buyers see."
    live = db.scalar(select(MarketplaceListing.id).where(
        MarketplaceListing.conversation_id == conv_id, MarketplaceListing.state.in_(LIVE_STATES)).limit(1))
    if live is not None:
        return "Live on a marketplace: generating would rewrite a listing buyers see."
    if not repo.get_photos(conv_id):
        return "No photos to generate from. Add its photos first."
    return None


def _generate_listings(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    from anteroom.services.listing_provider import get_listing_provider

    ids = _conversation_ids(args)
    if get_listing_provider() is None:
        raise GenerationToolError("Connect an AI provider in Settings to generate listings.")
    skipped: list[dict[str, str]] = []
    ready: list[str] = []
    for conv_id in ids:
        reason = _why_not(db, conv_id)
        if reason is not None:
            skipped.append({"conversation_id": conv_id, "reason": reason})
        else:
            ready.append(conv_id)
    db.rollback()
    if current() is None:
        # On the Mac generation runs in this process: start it once the session is closed.
        return {"_after": [{_PLACEHOLDER: {"ready": ready, "skipped": skipped}}]} if ready else _result([], skipped)
    started = []
    take = submission_limit.get()
    for conv_id in ready:
        try:
            if take is not None:
                take()
            _queue(db, conv_id)
        except HTTPException as exc:
            db.rollback()
            skipped.append({"conversation_id": conv_id, "reason": _reason(exc)})
        else:
            started.append(conv_id)
    return _result(started, skipped)


def _queue(db: Session, conv_id: str) -> None:
    """The seller's durable draft task, as the hosted editor's Generate queues it."""
    from anteroom.services import background_tasks as queue

    queue.enqueue(db, conv_id, "generate", queue.draft_payload(db, conv_id))


def _reason(exc: HTTPException) -> str:
    if exc.status_code == 409:
        return "Already generating."
    if exc.status_code == 429:
        return "Not started: too many drafts started in a short time. Try this one again in a moment."
    return str(exc.detail)


def _result(started: list[str], skipped: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "started": started,
        "skipped": skipped,
        "published": False,
        "pushed_to_marketplaces": False,
        "note": _NOTE if started else "Nothing was started.",
    }


HANDLERS = {"generate_listings": _generate_listings}


def generation_state(db: Session, conv_id: str) -> dict[str, Any]:
    """Where this listing's generation is, for get_listing.

    Hosted generation is the seller's durable draft task: a queued one leaves the
    listing a draft until a worker starts it, so the task says what is happening
    and why it failed. On the Mac a run is in this process while it lasts.
    """
    if current() is not None:
        from anteroom.models.background_task import BackgroundTask

        task = db.scalar(select(BackgroundTask).where(
            BackgroundTask.conversation_id == conv_id, BackgroundTask.kind == "generate",
        ).order_by(BackgroundTask.created_at.desc()).limit(1))
        if task is None:
            return {"state": "idle"}
        return {"state": task.status, "error": task.error, "updated_at": task.updated_at}
    from anteroom.services.streaming import active_generation

    conv = ConversationRepo(db).get(conv_id)
    running = active_generation(conv_id) is not None or (conv is not None and conv.status == "in_progress")
    return {"state": "running" if running else "idle"}


def pending_generations(body: Any) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """(tool result, drafts to start) for each call that still has to start generating (the Mac)."""
    found = []
    for response in body if isinstance(body, list) else [body]:
        result = response.get("result") if isinstance(response, dict) else None
        if isinstance(result, dict):
            for item in result.get("content", []):
                if isinstance(item, dict) and _PLACEHOLDER in item:
                    found.append((result, item[_PLACEHOLDER]))
    return found


def finish(result: dict[str, Any], spec: dict[str, Any], started: list[str], skipped: list[dict[str, str]]) -> None:
    """Write what actually started into the call's result."""
    data = _result(started, [*spec["skipped"], *skipped])
    result["content"] = [{"type": "text", "text": json.dumps(data, indent=2)}]
    result["isError"] = False
