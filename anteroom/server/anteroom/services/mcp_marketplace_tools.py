"""MCP tools that act on marketplaces: save a draft there, List, Update, Delist, and read their status.

Approved by the seller on 2026-11-24 (AGENTS.md, **Invariant**): an agent may
save marketplace drafts, publish, update live listings and delist only when the
seller has granted its access token or connected app the ``marketplaces``
permission (off by default, ``services/mcp_tokens.py``). Each call names its
marketplaces; publish, update and delist also carry ``confirm``. The seller's
own session and an agent on the Mac without a token never reach these tools:
they need that grant.

Each action runs the service the app's own buttons run, with the same checks:
``native_marketplaces`` (save draft, List, Update) and ``marketplace_delist``
(Delist), and Scheduled Draft Publishing for a later ``publish_at``. Nothing here
retries, queues or acts on every marketplace.

The MCP route runs a tool call inside one database session with a deadline.
These handlers check the call there, then hand back a pending action; the route
runs it after that session has closed (``run_pending``), because the services
wait on Chrome, a cloud session, eBay or Etsy. Whatever has not answered by the
call's deadline keeps running, and the result says so instead of guessing.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from time import monotonic
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy.orm import Session

from anteroom.models.mcp_token import McpAccessToken, McpOAuthConnection
from anteroom.repositories.queries import ConversationRepo, JobRepo, ListingRepo

log = logging.getLogger("anteroom.mcp_marketplace_tools")

MARKETPLACES = ("ebay", "etsy", "poshmark", "mercari", "depop")
NAMES = {"ebay": "eBay", "etsy": "Etsy", "poshmark": "Poshmark", "mercari": "Mercari", "depop": "Depop"}
# Areas the seller must grant a token or app by name. The seller's own session
# and loopback on the Mac without a token reach every other tool, never these.
GRANTED_ONLY_AREAS = frozenset({"marketplaces"})
# Marks a tool result whose marketplace work runs after the call's database session closes.
PENDING_KEY = "_anteroom_marketplace_action"
# What a pending result says if it is never run: nothing was started.
NOT_STARTED = "Anteroom did not start this marketplace action. Nothing changed on any marketplace."
MAX_JOBS_SHOWN = 10
# A marketplace action starts only with at least this much of the call left to report on it.
MIN_START_SECONDS = 1.0

_MARKETPLACES_SCHEMA = {
    "type": "array",
    "items": {"type": "string", "enum": list(MARKETPLACES)},
    "minItems": 1,
    "maxItems": len(MARKETPLACES),
    "uniqueItems": True,
    "description": "Each marketplace to act on, by name. There is no 'all'.",
}
_CONFIRM_SCHEMA = {
    "type": "boolean",
    "description": "Must be true: the seller asked for exactly this action on exactly these marketplaces.",
}
_REVISION_SCHEMA = {
    "type": "string",
    "description": "current_revision_id from get_listing. Refused if the listing changed since.",
}


def _annotations(*, destructive: bool) -> dict[str, Any]:
    # Open world: each reaches a marketplace. Clients ask the seller before running these.
    return {"readOnlyHint": False, "destructiveHint": destructive, "idempotentHint": False, "openWorldHint": True}


TOOLS: list[dict[str, Any]] = [
    {
        "name": "save_marketplace_draft",
        "title": "Save marketplace draft",
        "description": (
            "Save the listing as a private draft on each named marketplace where it is not listed yet. "
            "Never publishes. Needs the Marketplaces permission set to Save drafts or higher. "
            "Returns once each draft has started saving; call get_action_status for the result."
        ),
        "annotations": _annotations(destructive=False),
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string", "description": "Listing / conversation ID."},
                "marketplaces": _MARKETPLACES_SCHEMA,
                "expected_revision_id": _REVISION_SCHEMA,
            },
            "required": ["conversation_id", "marketplaces", "expected_revision_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "publish_listing",
        "title": "Publish listing",
        "description": (
            "List the saved listing on each named marketplace, now or at publish_at. Only when the seller "
            "asked for it: requires confirm=true and the Marketplaces permission set to Publish, update and "
            "delist. Publishing now returns once each listing has started; it is not live until "
            "get_action_status reports it published. publish_at schedules it with Scheduled Draft Publishing "
            "instead (marks it Ready to list); editing the listing later unschedules it."
        ),
        "annotations": _annotations(destructive=True),
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string", "description": "Listing / conversation ID."},
                "marketplaces": _MARKETPLACES_SCHEMA,
                "expected_revision_id": _REVISION_SCHEMA,
                "confirm": _CONFIRM_SCHEMA,
                "publish_at": {
                    "type": "string",
                    "format": "date-time",
                    "description": "Optional future time (ISO 8601 with a time zone) to publish instead of now.",
                },
            },
            "required": ["conversation_id", "marketplaces", "expected_revision_id", "confirm"],
            "additionalProperties": False,
        },
    },
    {
        "name": "send_unsent_edits",
        "title": "Send unsent edits",
        "description": (
            "Write the listing saved in Anteroom onto its live listing on each named marketplace. Only when the "
            "seller asked for it: requires confirm=true and the Marketplaces permission set to Publish, update "
            "and delist. Never lists again or creates a second listing. Returns once each update has started; "
            "call get_action_status for the result."
        ),
        "annotations": _annotations(destructive=True),
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string", "description": "Listing / conversation ID."},
                "marketplaces": _MARKETPLACES_SCHEMA,
                "expected_revision_id": _REVISION_SCHEMA,
                "confirm": _CONFIRM_SCHEMA,
            },
            "required": ["conversation_id", "marketplaces", "expected_revision_id", "confirm"],
            "additionalProperties": False,
        },
    },
    {
        "name": "delist_listing",
        "title": "Delist listing",
        "description": (
            "End the item's live listing on each named marketplace. Only when the seller asked for it: requires "
            "confirm=true and the Marketplaces permission set to Publish, update and delist. A listing that sold "
            "is never ended. Reports each delist the marketplace confirmed; anything still running is reported "
            "as running, to check with get_action_status."
        ),
        "annotations": _annotations(destructive=True),
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string", "description": "Listing / conversation ID."},
                "marketplaces": _MARKETPLACES_SCHEMA,
                "confirm": _CONFIRM_SCHEMA,
            },
            "required": ["conversation_id", "marketplaces", "confirm"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_action_status",
        "title": "Get marketplace action status",
        "description": (
            "What each marketplace action on a listing is doing: running, published, draft saved, delisted, "
            "failed with the marketplace's reason, or unconfirmed with what the seller should check. Also its "
            "scheduled publish and recent jobs, with the agent that started each. Read-only."
        ),
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        "inputSchema": {
            "type": "object",
            "properties": {"conversation_id": {"type": "string", "description": "Listing / conversation ID."}},
            "required": ["conversation_id"],
            "additionalProperties": False,
        },
    },
]

# The permission area and level each tool needs (``services/mcp_tokens.py``).
PERMISSIONS: dict[str, tuple[str, str]] = {
    "save_marketplace_draft": ("marketplaces", "draft"),
    "publish_listing": ("marketplaces", "publish"),
    "send_unsent_edits": ("marketplaces", "publish"),
    "delist_listing": ("marketplaces", "publish"),
    "get_action_status": ("listings", "read"),
}


class MarketplaceToolError(ValueError):
    """A refusal the agent reads, as an MCP tool error."""


# --- who is asking ------------------------------------------------------------

def agent_label(db: Session, *, token_id: str | None, oauth_client_id: str | None) -> str | None:
    """How jobs an agent starts name it: ``mcp:`` and the token's or connected app's name."""
    name: str | None = None
    if oauth_client_id is not None:
        row = db.query(McpOAuthConnection).filter(McpOAuthConnection.client_id == oauth_client_id).first()
        name = (row.client_name if row else None) or "Connected app"
    elif token_id is not None:
        token = db.get(McpAccessToken, token_id)
        name = (token.name if token else None) or "MCP token"
    return f"mcp:{name}"[:120] if name else None


def agent_name(requested_by: str | None) -> str | None:
    """The agent's name in a ``requested_by`` value, for the seller to read."""
    if not requested_by or not requested_by.startswith("mcp:"):
        return None
    return requested_by.removeprefix("mcp:") or "AI assistant"


# --- argument checks (in the call's database session) --------------------------

def _require_str(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MarketplaceToolError(f"{key} is required.")
    return value.strip()[:500]


def _marketplaces(args: dict[str, Any]) -> list[str]:
    raw = args.get("marketplaces")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise MarketplaceToolError(f"Name each marketplace to act on: {', '.join(MARKETPLACES)}.")
    names: list[str] = []
    for value in raw:
        name = str(value or "").strip().lower()
        if name in {"all", "every", "everywhere", "*"}:
            raise MarketplaceToolError("Name each marketplace. Anteroom never acts on every marketplace at once.")
        if name not in MARKETPLACES:
            raise MarketplaceToolError(f"Unknown marketplace: {value}. Use: {', '.join(MARKETPLACES)}.")
        if name not in names:
            names.append(name)
    return names


def _require_confirm(args: dict[str, Any], action: str) -> None:
    if args.get("confirm") is not True:
        raise MarketplaceToolError(
            f"Set confirm=true only once the seller has asked you to {action} on these marketplaces. Nothing changed."
        )


def _conversation(db: Session, conv_id: str):
    conv = ConversationRepo(db).get(conv_id)
    if conv is None:
        raise MarketplaceToolError("Listing not found.")
    return conv


def _require_revision(db: Session, conv_id: str, args: dict[str, Any]) -> str:
    expected = _require_str(args, "expected_revision_id")
    listing = ListingRepo(db).get_current(conv_id)
    if listing is None or not listing.current_revision_id:
        raise MarketplaceToolError("This listing has no saved version yet. Nothing changed.")
    if listing.current_revision_id != expected:
        raise MarketplaceToolError(
            "The listing changed since you read it. Call get_listing, check it again, and use its "
            "current_revision_id. Nothing changed."
        )
    return expected


def _refuse_in_sandbox() -> None:
    from anteroom import sandbox

    if sandbox.enabled():
        raise MarketplaceToolError(sandbox.LISTING_DETAIL)


def _pending(kind: str, conv_id: str, names: list[str], agent: str | None, revision_id: str | None = None) -> dict:
    return {PENDING_KEY: {"kind": kind, "conversation_id": conv_id, "marketplaces": names,
                          "revision_id": revision_id, "agent": agent}}


# --- handlers -------------------------------------------------------------------

def _save_marketplace_draft(db: Session, args: dict[str, Any], *, agent: str | None) -> dict[str, Any]:
    conv_id = _require_str(args, "conversation_id")
    names = _marketplaces(args)
    _conversation(db, conv_id)
    revision_id = _require_revision(db, conv_id, args)
    return _pending("save_draft", conv_id, names, agent, revision_id)


def _publish_listing(db: Session, args: dict[str, Any], *, agent: str | None) -> dict[str, Any]:
    conv_id = _require_str(args, "conversation_id")
    names = _marketplaces(args)
    _require_confirm(args, "publish this listing")
    conv = _conversation(db, conv_id)
    revision_id = _require_revision(db, conv_id, args)
    publish_at = args.get("publish_at")
    if publish_at is None:
        _refuse_in_sandbox()
        return _pending("list", conv_id, names, agent, revision_id)
    if not isinstance(publish_at, str) or not publish_at.strip():
        raise MarketplaceToolError("publish_at must be an ISO 8601 date and time, e.g. 2026-12-01T09:00:00-05:00.")
    from anteroom.services import draft_scheduling

    try:
        when = datetime.fromisoformat(publish_at.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketplaceToolError("publish_at must be an ISO 8601 date and time, e.g. 2026-12-01T09:00:00-05:00.") from exc
    if when.tzinfo is None:
        raise MarketplaceToolError("publish_at needs a time zone, e.g. 2026-12-01T09:00:00-05:00 or ...Z for UTC.")
    try:
        view = draft_scheduling.schedule_draft(
            db, conv_id, publish_at=when, marketplaces=names, confirm=True, requested_by=agent,
        )
    except draft_scheduling.SchedulePublishError as exc:
        raise MarketplaceToolError(f"{exc.detail} Nothing was scheduled.") from exc
    return {
        "ok": True,
        "conversation_id": conv.id,
        "outcome": "scheduled",
        "message": (
            f"Scheduled to list on {_names(names)} at {view['scheduled_publish_at']} (UTC). Nothing is published "
            "yet. Editing the listing before then unschedules it; call get_action_status to follow it."
        ),
        "schedule": view,
    }


def _send_unsent_edits(db: Session, args: dict[str, Any], *, agent: str | None) -> dict[str, Any]:
    conv_id = _require_str(args, "conversation_id")
    names = _marketplaces(args)
    _require_confirm(args, "update the live listings")
    _conversation(db, conv_id)
    revision_id = _require_revision(db, conv_id, args)
    _refuse_in_sandbox()
    return _pending("update", conv_id, names, agent, revision_id)


def _delist_listing(db: Session, args: dict[str, Any], *, agent: str | None) -> dict[str, Any]:
    conv_id = _require_str(args, "conversation_id")
    names = _marketplaces(args)
    _require_confirm(args, "delist this item")
    _conversation(db, conv_id)
    _refuse_in_sandbox()
    return _pending("delist", conv_id, names, agent)


def _get_action_status(db: Session, args: dict[str, Any], *, agent: str | None = None) -> dict[str, Any]:
    from anteroom.services import draft_scheduling, marketplace_delist
    from anteroom.services import native_marketplaces as native

    conv_id = _require_str(args, "conversation_id")
    conv = _conversation(db, conv_id)
    records = native.status(conv_id, db, conv=conv)
    live = marketplace_delist.listings(db, conv_id, conv=conv)
    jobs = JobRepo(db).list_summaries(conv_id)[:MAX_JOBS_SHOWN]
    return {
        "conversation_id": conv_id,
        "item_status": conv.status,
        "marketplaces": {
            name: summary for name in MARKETPLACES
            if (summary := marketplace_status(name, records.get(name) or {}, live.get(name)))
        },
        "schedule": draft_scheduling.schedule_view(ListingRepo(db).get_current(conv_id), conv),
        "recent_jobs": [
            {
                "job_id": job.id,
                "action": _job_label(job.current_step),
                "status": job.status,
                "error": job.last_error,
                "started_by": agent_name(getattr(job, "requested_by", None)) or "seller",
                "created_at": job.created_at,
                "updated_at": job.updated_at,
            }
            for job in jobs
        ],
    }


HANDLERS = {
    "save_marketplace_draft": _save_marketplace_draft,
    "publish_listing": _publish_listing,
    "send_unsent_edits": _send_unsent_edits,
    "delist_listing": _delist_listing,
    "get_action_status": _get_action_status,
}


# --- status -----------------------------------------------------------------------

def _names(names: list[str]) -> str:
    return ", ".join(NAMES[name] for name in names)


def _job_label(step: str | None) -> str:
    from anteroom.services.native_marketplaces import step_label

    if step == "native_delist":
        return "Delisting"
    return (step_label(step or "") or step or "").removesuffix("…")


def _check_text(name: str) -> str:
    return (f"Anteroom could not confirm what happened on {name}. Ask the seller to look for this item in their "
            f"{name} listings and drafts, then press Check on the {name} row in Anteroom. Do not try again before that.")


def marketplace_status(marketplace: str, record: dict, live: dict | None) -> dict[str, Any] | None:
    """One marketplace's state for an agent, in a fixed vocabulary; None when nothing is known there."""
    name = NAMES[marketplace]
    state = record.get("state")
    error = {key: record.get(key) for key in ("error", "error_code", "marketplace_message", "error_fields") if record.get(key)}
    if live and live.get("state") == "deleting":
        return {"status": "delisting", "message": f"Delisting on {name} is running."}
    if live and live.get("state") == "failed":
        return {"status": "delist_failed", "url": live.get("url"), "error": live.get("error"),
                "marketplace_message": live.get("marketplace_message"),
                "message": f"The {name} delist did not finish; the listing may still be live. Delisting again is safe."}
    if record.get("running"):
        progress = record.get("progress") or {}
        return {"status": "running", "action": record.get("action"), "step": progress.get("label"),
                "message": f"{name}: {progress.get('label') or 'working'}. Not finished yet."}
    if state == "unknown":
        return {"status": "uncertain", "action": record.get("action"), **error, "what_to_check": _check_text(name)}
    if live and live.get("sold_here"):
        return {"status": "sold", "url": live.get("url")}
    if live and live.get("state") == "deleted":
        return {"status": "delisted", "url": live.get("url"),
                "message": f"{name} confirmed the listing is gone." if not live.get("already_gone")
                else f"{name} reports the listing was already gone."}
    if state in {"published", "pending_review"} or (live and live.get("state") == "live"):
        status = "pending_review" if state == "pending_review" else "published"
        return {"status": status, "url": (live or {}).get("url") or record.get("listing_url"), **error,
                "message": f"{name} is still reviewing the listing." if status == "pending_review"
                else f"Live on {name}."}
    if state == "draft":
        return {"status": "draft_saved", "draft_url": record.get("draft_url"), **error,
                "message": f"A private draft is saved on {name}. Nothing is published there."}
    if error:
        return {"status": "failed", "action": record.get("action"), **error,
                "message": f"The last {name} action did not go through. Fix what the error names, then try again."}
    return None


# --- running the marketplace work (after the call's session closes) ---------------

def pending_actions(body: Any) -> list[dict[str, Any]]:
    """The tool results in a JSON-RPC reply that still have marketplace work to run."""
    messages = body if isinstance(body, list) else [body]
    found = []
    for message in messages:
        result = message.get("result") if isinstance(message, dict) else None
        if isinstance(result, dict) and isinstance(result.get(PENDING_KEY), dict):
            found.append(result)
    return found


def pending_result(spec: dict[str, Any]) -> dict[str, Any]:
    """A tool result whose work has not run yet. Until it runs it says nothing started."""
    return {"content": [{"type": "text", "text": NOT_STARTED}], "isError": True, PENDING_KEY: spec}


async def run_pending(results: list[dict[str, Any]], *, deadline: float) -> None:
    """Run each pending marketplace action, filling in its result. ``deadline`` is a ``time.monotonic()`` value."""
    for result in results:
        spec = result.pop(PENDING_KEY)
        if deadline - monotonic() < MIN_START_SECONDS:
            # Too little of the call is left to say what happened: start nothing.
            continue
        try:
            payload, started = await _run(spec, deadline)
        except Exception:  # noqa: BLE001 - the agent is told nothing was confirmed, never success
            log.exception("MCP marketplace action %s failed before it could report", spec.get("kind"))
            payload, started = {"ok": False, "message": (
                "Anteroom could not report what this action did. Call get_action_status before trying again.")}, False
        result["content"] = [{"type": "text", "text": json.dumps(payload, indent=2, default=str)}]
        result["isError"] = not started


def _refusal(exc: BaseException) -> str | None:
    """The seller-facing reason a service refused, or None for an unexpected failure."""
    from anteroom.services.marketplace_delist import DelistError
    from anteroom.services.native_marketplaces import NativeMarketplaceError

    if isinstance(exc, NativeMarketplaceError | DelistError):
        return str(exc)
    if isinstance(exc, HTTPException):
        return exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return None


async def _wait(tasks: list[asyncio.Task], deadline: float) -> None:
    # Never cancels: work still running at the deadline keeps going, and is reported as running.
    remaining = deadline - monotonic()
    if remaining > 0 and tasks:
        await asyncio.wait(tasks, timeout=remaining)


async def _run(spec: dict[str, Any], deadline: float) -> tuple[dict[str, Any], bool]:
    if spec["kind"] == "delist":
        return await _run_delist(spec, deadline)
    return await _run_native(spec, deadline)


_STARTED = {
    "save_draft": "Saving a private draft on {name} started. Nothing is published.",
    "list": "Listing on {name} started. It is not live until get_action_status reports it published.",
    "update": "Updating the live {name} listing started. It is not updated until get_action_status says so.",
}


async def _run_native(spec: dict[str, Any], deadline: float) -> tuple[dict[str, Any], bool]:
    from anteroom.services import native_marketplaces as native
    from anteroom.services.streaming import spawn

    kind, conv_id, agent = spec["kind"], spec["conversation_id"], spec["agent"]
    body = native.ListRequest(revision_id=spec["revision_id"], confirm=kind != "save_draft")

    def start(name: str):
        # Checked against MARKETPLACES when the call came in.
        marketplace = cast(native.Marketplace, name)
        if kind == "save_draft":
            return native.save_draft(conv_id, marketplace, spec["revision_id"], requested_by=agent)
        if kind == "update":
            return native.update_live(conv_id, marketplace, body, requested_by=agent)
        return native.list_now(conv_id, marketplace, body, requested_by=agent)

    # Each marketplace starts side by side, as the app's own buttons do; each keeps its own slot.
    tasks = {marketplace: spawn(start(marketplace)) for marketplace in spec["marketplaces"]}
    await _wait(list(tasks.values()), deadline)
    outcomes: dict[str, dict[str, Any]] = {}
    for marketplace, task in tasks.items():
        name = NAMES[marketplace]
        if not task.done():
            task.add_done_callback(_consume)
            outcomes[marketplace] = {"status": "unconfirmed", "message": (
                f"{name} had not answered when this call's time ran out. It may still start. Call "
                "get_action_status before trying again.")}
        elif (exc := task.exception()) is not None:
            reason = _refusal(exc)
            if reason is None:
                log.error("MCP %s on %s failed: %r", kind, marketplace, exc)
                outcomes[marketplace] = {"status": "error", "message": (
                    f"Anteroom hit an error starting this on {name}. Call get_action_status to see whether "
                    "anything started before trying again.")}
            else:
                outcomes[marketplace] = {"status": "refused", "error": reason,
                                         "message": f"Nothing was started on {name}."}
        else:
            record = task.result()
            outcomes[marketplace] = {"status": "started", "state": record.get("state"), "job_id": record.get("job_id"),
                                     "message": _STARTED[kind].format(name=name)}
    started = any(outcome["status"] in {"started", "unconfirmed"} for outcome in outcomes.values())
    return {"ok": started, "conversation_id": conv_id, "action": kind, "marketplaces": outcomes}, started


async def _run_delist(spec: dict[str, Any], deadline: float) -> tuple[dict[str, Any], bool]:
    from anteroom.services import marketplace_delist
    from anteroom.services.streaming import spawn

    conv_id, names = spec["conversation_id"], spec["marketplaces"]
    task = spawn(marketplace_delist.delist(conv_id, names, True, requested_by=spec["agent"]))
    await _wait([task], deadline)
    if not task.done():
        task.add_done_callback(_consume)
        return {"ok": True, "conversation_id": conv_id, "action": "delist", "outcome": "running", "message": (
            f"The delist on {_names(names)} is still running; nothing is confirmed yet. Call get_action_status "
            "for each marketplace's result before trying again.")}, True
    if (exc := task.exception()) is not None:
        reason = _refusal(exc)
        if reason is None:
            log.error("MCP delist failed: %r", exc)
            reason = "Anteroom hit an error. Call get_action_status to see whether anything was delisted."
        return {"ok": False, "conversation_id": conv_id, "action": "delist", "error": reason,
                "message": "The delist did not run to the end."}, False
    listings = task.result()
    outcomes = {}
    for name in names:
        entry = listings.get(name) or {}
        state = entry.get("state")
        if state == "deleted":
            outcomes[name] = {"status": "delisted", "message": f"{NAMES[name]} confirmed the listing is gone."}
        elif state == "failed":
            outcomes[name] = {"status": "failed", "error": entry.get("error"),
                              "marketplace_message": entry.get("marketplace_message"),
                              "message": f"{NAMES[name]} did not confirm the delist; the listing may still be live."}
        elif not entry:
            outcomes[name] = {"status": "delisted", "message": f"{NAMES[name]} no longer has the listing."}
        else:
            outcomes[name] = {"status": "unconfirmed", "message": (
                f"Anteroom could not confirm the {NAMES[name]} delist. Call get_action_status.")}
    confirmed = any(outcome["status"] == "delisted" for outcome in outcomes.values())
    return {"ok": confirmed, "conversation_id": conv_id, "action": "delist", "marketplaces": outcomes}, confirmed


def _consume(task: asyncio.Task) -> None:
    """Read a detached task's failure, so it is logged once instead of as an unretrieved exception."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None and _refusal(exc) is None:
        log.error("MCP marketplace action failed after the call returned: %r", exc)
