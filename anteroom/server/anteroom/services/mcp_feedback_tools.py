"""``send_feedback``: a seller's AI assistant sends feedback to the Anteroom team.

Every item is saved to the seller's ``mcp_feedback`` table first. On the
hosted app with operator email on (``ANTEROOM_ERROR_EMAIL=1``, never in a
sandbox), it is then emailed to the operator inbox through the same relay
and shared budget as error emails (``services/error_alerts.py``). The email
is sent after the call's database session closes, with a deadline, and the
tool result says what actually happened.

The Mac app never sends email itself. There the feedback is saved and the
result says plainly it was not sent, with an email link the seller can use.

The email carries the redacted message, its kind, the seller id, the token or
app name (never the token), release and environment, and a listing ID when
given. Never listing contents, photos, buyer data or secrets.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from anteroom.models.mcp_feedback import McpFeedback
from anteroom.repositories.queries import ConversationRepo
from anteroom.services import error_alerts, error_email_delivery
from anteroom.services.mcp_tokens import ANY_AREA
from anteroom.services.redaction import redact
from anteroom.services.service_context import current
from anteroom.version import app_version

log = logging.getLogger(__name__)

KINDS = {"bug": "Bug", "feature_request": "Feature request", "question": "Question", "other": "Other"}
MAX_MESSAGE = 4000
# A seller asks for feedback to be sent now and then; a looping assistant
# sends the same thing over and over. Enough for the first, not the second.
MAX_PER_HOUR = 5
SUPPORT_EMAIL = "support@myanteroom.com"
# How long the call waits for the relay, and how long each SMTP step may take.
SEND_WAIT_SECONDS = 15.0
SMTP_TIMEOUT_SECONDS = 10.0
# Marks the content item standing in for the email until the session closes.
_PLACEHOLDER = "_anteroom_feedback"
_send_email = error_alerts.send_email

TOOLS: list[dict[str, Any]] = [
    {
        "name": "send_feedback",
        "title": "Send feedback to Anteroom",
        "description": (
            "Send the seller's feedback about Anteroom to the Anteroom team: a bug, a feature "
            "request, a question, or something confusing. Send only what the seller asked you "
            "to send; never send feedback on your own initiative. Show the seller the exact "
            "message first and send it only once they agree. Leave out buyer names and "
            "addresses, passwords, tokens and other secrets; give a listing's conversation_id "
            "instead of copying its contents. The result says whether the email reached the "
            "team. Tell the seller exactly that; if it was not sent, give them the email link "
            "from the result."
        ),
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string", "minLength": 1, "maxLength": MAX_MESSAGE,
                    "description": "The feedback, as the seller approved it.",
                },
                "kind": {"type": "string", "enum": list(KINDS), "description": "What sort of feedback this is."},
                "conversation_id": {
                    "type": "string", "description": "Optional: the listing the feedback is about (its ID only).",
                },
            },
            "required": ["message", "kind"],
            "additionalProperties": False,
        },
    },
]

# It changes no listing and reveals nothing, so any access at all is enough.
PERMISSIONS: dict[str, tuple[str, str]] = {"send_feedback": (ANY_AREA, "read")}


def _hosted() -> bool:
    return current() is not None


def _mail_enabled() -> bool:
    return (_hosted() and os.environ.get("ANTEROOM_ERROR_EMAIL") == "1"
            and os.environ.get("ANTEROOM_SANDBOX") != "1")


def _daily_limit() -> int:
    try:
        return int(os.environ.get("ANTEROOM_ERROR_EMAIL_DAILY_LIMIT", "0"))
    except ValueError:
        return 0


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _source(agent: str | None) -> str:
    """The token or connected app's name as the seller gave it; never the token.

    ``agent`` is ``mcp:`` and that name (``mcp_marketplace_tools.agent_label``),
    or None for the seller's own session or the Mac without a token.
    """
    if agent:
        name = f"AI assistant “{agent.removeprefix('mcp:') or 'unnamed'}”"
    else:
        name = "The seller's own session" if _hosted() else "Anteroom on this Mac, without a token"
    return redact(name)[:200]


def _check_rate(db: Session, now: datetime) -> None:
    cutoff = now - timedelta(hours=1)
    count, oldest = db.execute(
        select(func.count(McpFeedback.id), func.min(McpFeedback.created_at)).where(McpFeedback.created_at > cutoff)
    ).one()
    if count >= MAX_PER_HOUR and oldest is not None:
        retry = _aware(oldest) + timedelta(hours=1)
        minutes = max(1, int((retry - now).total_seconds() // 60) + 1)
        raise ValueError(
            f"Feedback was already sent {count} times in the last hour, the most Anteroom accepts. "
            f"Nothing was sent. Try again in about {minutes} minutes."
        )


def _environment() -> str:
    if os.environ.get("ANTEROOM_SANDBOX") == "1":
        return "sandbox"
    if not _hosted():
        return "Mac app"
    region = os.environ.get("FLY_REGION")
    return f"hosted ({region})" if region else "hosted"


def _email(row: McpFeedback, seller_id: str) -> tuple[str, str]:
    first_line = row.message.strip().splitlines()[0][:100] if row.message.strip() else ""
    subject = f"[Anteroom feedback] {KINDS[row.kind]}: {first_line}"[:200]
    lines = [
        f"Kind         {KINDS[row.kind]}",
        f"Seller id    {seller_id}",
        f"Sent by      {row.source}",
        f"Listing id   {row.conversation_id or '-'}",
        f"App version  {app_version()}",
        f"Release      {error_alerts.current_release()}",
        f"Environment  {_environment()}",
        f"Feedback id  {row.id}",
        f"When         {_aware(row.created_at).isoformat(timespec='seconds')}",
        "", "Message", "-------", row.message,
    ]
    return subject, "\n".join(lines)


def _mailto(row: McpFeedback) -> str:
    subject = f"Anteroom feedback ({KINDS[row.kind]})"
    body = "\n".join([
        row.message, "",
        f"Kind: {KINDS[row.kind]}",
        f"Listing id: {row.conversation_id or '-'}",
        f"App version: {app_version()} ({_environment()})",
        f"Feedback id: {row.id}",
    ])
    return f"mailto:{SUPPORT_EMAIL}?subject={quote(subject)}&body={quote(body)}"


def _send_it_yourself(mailto: str) -> dict[str, str]:
    return {"email": SUPPORT_EMAIL, "mailto": mailto}


def _tool_send_feedback(
    db: Session, args: dict[str, Any], *, agent: str | None = None, **_kw: Any,
) -> dict[str, Any]:
    message = args.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message is required: the feedback, as the seller approved it.")
    if len(message) > MAX_MESSAGE:
        raise ValueError(f"message must be {MAX_MESSAGE} characters or fewer.")
    kind = args.get("kind")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of: {', '.join(KINDS)}.")
    conv_id = args.get("conversation_id")
    if conv_id is not None:
        if not isinstance(conv_id, str) or not conv_id.strip() or len(conv_id) > 64:
            raise ValueError("conversation_id must be a listing ID.")
        conv_id = conv_id.strip()
        if ConversationRepo(db).get(conv_id) is None:
            raise ValueError("Listing not found.")

    now = datetime.now(UTC)
    _check_rate(db, now)
    sending = _mail_enabled()
    row = McpFeedback(
        kind=kind,
        # Postgres text rejects NUL characters.
        message=redact(message.strip().replace("\x00", "")),
        conversation_id=conv_id,
        source=_source(agent),
        delivery="sending" if sending else "not_sent",
        created_at=now,
    )
    db.add(row)
    db.commit()
    result: dict[str, Any] = {
        "feedback_id": row.id,
        "saved": True,
        "kind": kind,
        "conversation_id": conv_id,
    }
    mailto = _mailto(row)
    if not sending:
        if os.environ.get("ANTEROOM_SANDBOX") == "1":
            note = "Saved in the sandbox. The sandbox never sends email, so the Anteroom team did not get it."
        elif not _hosted():
            note = (
                "Saved in Anteroom on this Mac, but not sent: the Mac app never sends email itself. "
                "To send it, the seller can open the email link below in their own email app, or "
                "connect this assistant to their myanteroom.com account, which emails feedback to "
                "the team directly."
            )
        else:
            note = "Saved, but not sent: Anteroom's team email is turned off here. The seller can use the email link below."
        return {**result, "delivered": False, "delivery": "not_sent", "note": note,
                "send_it_yourself": _send_it_yourself(mailto)}

    seller = current()
    subject, body = _email(row, seller.seller_id if seller is not None else "-")
    return {
        **result,
        "delivered": False,
        "delivery": "sending",
        "_content": [{
            "type": "text",
            "text": "Sending…",
            _PLACEHOLDER: {"id": row.id, "subject": subject, "body": body, "mailto": mailto},
        }],
    }


HANDLERS = {"send_feedback": _tool_send_feedback}


def pending_deliveries(body: Any) -> list[tuple[list[Any], int]]:
    """Where a response still holds a feedback email to send, as (content list, index) pairs."""
    responses = body if isinstance(body, list) else [body]
    found = []
    for response in responses:
        result = response.get("result") if isinstance(response, dict) else None
        content = result.get("content") if isinstance(result, dict) else None
        if isinstance(content, list):
            found.extend(
                (content, index) for index, item in enumerate(content)
                if isinstance(item, dict) and _PLACEHOLDER in item
            )
    return found


def _record(feedback_id: str, delivery: str) -> None:
    from anteroom.database import SessionLocal

    with SessionLocal() as db:
        row = db.get(McpFeedback, feedback_id)
        if row is None:
            return
        row.delivery = delivery
        if delivery == "sent":
            row.delivered_at = datetime.now(UTC)
        db.commit()


def _send(feedback_id: str, subject: str, body: str) -> str:
    """Reserve a slot in the shared mail budget, send, and record the outcome.

    Runs on a worker thread with no session open across the SMTP exchange. It
    records the outcome itself, so an email that finishes after the call has
    answered is still marked sent.
    """
    from anteroom.database import engine
    from anteroom.repositories.error_email import EmailStore

    store = EmailStore(engine)
    try:
        reserved = error_email_delivery.reserve_direct_send(store, _daily_limit())
    except Exception:  # noqa: BLE001 - the budget is unreadable: do not send around it
        log.warning("Feedback %s not sent: the operator mail budget could not be read", feedback_id, exc_info=True)
        reserved = False
    if not reserved:
        outcome = "not_sent"
    else:
        try:
            _send_email(subject, body, sender="Anteroom Feedback", timeout=SMTP_TIMEOUT_SECONDS)
            outcome = "sent"
        except Exception as exc:  # noqa: BLE001 - reported to the assistant and recorded
            outcome = "failed"
            log.warning("Feedback %s email failed (%s)", feedback_id, type(exc).__name__)
            try:
                error_email_delivery.record_direct_failure(store, exc)
            except Exception:  # noqa: BLE001 - the failure is already reported
                pass
    try:
        _record(feedback_id, outcome)
    except Exception:  # noqa: BLE001 - the email outcome stands; the row keeps "sending"
        log.warning("Feedback %s delivery could not be recorded", feedback_id, exc_info=True)
    return outcome


_NOTES = {
    "sent": "Saved and emailed to the Anteroom team.",
    "failed": "Saved, but the email to the Anteroom team failed. It stays saved in Anteroom; the seller can also send it with the email link below.",
    "not_sent": "Saved, but not emailed: Anteroom's team mailbox is not accepting messages right now. It stays saved in Anteroom; the seller can also send it with the email link below.",
    "unconfirmed": "Saved. The email to the Anteroom team had not finished sending when Anteroom answered, so it may or may not arrive. The seller can also send it with the email link below.",
}


def _apply(content: list[Any], index: int, outcome: str) -> None:
    marker = content.pop(index)[_PLACEHOLDER]
    try:
        data = json.loads(content[0]["text"])
    except (IndexError, KeyError, TypeError, ValueError):
        data = {}
    data.update({"delivered": outcome == "sent", "delivery": outcome, "note": _NOTES[outcome]})
    if outcome != "sent":
        data["send_it_yourself"] = _send_it_yourself(marker["mailto"])
    if content:
        content[0] = {"type": "text", "text": json.dumps(data, indent=2, default=str)}
    else:
        content.append({"type": "text", "text": json.dumps(data, indent=2, default=str)})


async def deliver_pending(pending: list[tuple[list[Any], int]], *, deadline: float) -> None:
    """Send each pending feedback email and write what happened into its tool result.

    Call only after the call's database session is closed. ``deadline`` is a
    ``time.monotonic()`` value the reply must not wait past.
    """
    async def one(content: list[Any], index: int) -> tuple[list[Any], int, str]:
        marker = content[index][_PLACEHOLDER]
        wait = min(SEND_WAIT_SECONDS, deadline - time.monotonic())
        work = asyncio.ensure_future(asyncio.to_thread(_send, marker["id"], marker["subject"], marker["body"]))
        try:
            outcome = await asyncio.wait_for(asyncio.shield(work), timeout=max(0.0, wait))
        except TimeoutError:
            # The thread keeps its own SMTP timeouts and records the outcome when it ends.
            outcome = "unconfirmed"
        return content, index, outcome

    outcomes = await asyncio.gather(*(one(content, index) for content, index in pending))
    # Remove markers from the end of each list first so earlier indexes stay valid.
    for content, index, outcome in sorted(outcomes, key=lambda item: item[1], reverse=True):
        _apply(content, index, outcome)
