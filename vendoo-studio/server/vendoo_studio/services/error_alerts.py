"""Email the operator a structured report for every distinct hosted error.

Every WARNING or ERROR record from Anteroom's loggers, uvicorn, asyncio and uncaught
thread or process exceptions becomes one report: what failed (exception type
and message), where (stack and call site), when, on which release and Machine,
for which request (method, route template, request id) and seller id. Browser
crashes arrive through ``routes/error_reports.py`` and are reported the same way.

Reports group by severity and fingerprint. A durable shared outbox batches
warnings hourly, groups repeated errors for 15 minutes, and reserves every
SMTP attempt against a configured rolling 24-hour budget before sending.

Messages and stacks pass through ``redaction.redact``; SQL parameters are
dropped. Delivery runs on its own thread, never on a request. Relay quota
rejections pause all senders for 24 hours and retain reports for later delivery.
Only the hosted app with ``ANTEROOM_ERROR_EMAIL=1`` sends; the Mac app and
sandboxes never do.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import queue
import re
import sys
import threading
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vendoo_studio.services.marketplace_diagnostics import sanitize_context
from vendoo_studio.services.redaction import redact
from vendoo_studio.version import app_version

OPERATOR = "ccanizares@myanteroom.com"
SMTP_HOST, SMTP_PORT = "smtp-relay.gmail.com", 587
MAX_MESSAGE = 2000
MAX_STACK = 12000
# Loggers whose errors are reported. Each is attached directly so a logger
# with propagate=False (uvicorn's, Anteroom's own on the Mac) is still covered.
LOGGERS = ("vendoo_studio", "uvicorn.error", "asyncio")
# The request middleware reports a route's unhandled exception with request
# context; uvicorn's copy of the same exception would be a duplicate.
_UVICORN_DUPLICATE = "Exception in ASGI application"
_SQL_PARAMETERS = re.compile(r"\[parameters: .*?\](?=\s*(?:\(Background|$))", re.DOTALL)
_SQL_STATEMENT = re.compile(r"\[SQL: .*?\](?=\s*(?:\[parameters|\(Background|$))", re.DOTALL)
# Anteroom's own IDs are 12 to 32 hex characters (uuid4().hex, often cut to 12);
# they must not split one failure into a group per job or field.
_VOLATILE = re.compile(
    r"0x[0-9a-f]+|\b[0-9a-f]{8}-[0-9a-f-]{27}\b|\b(?=[0-9a-f]*\d)[0-9a-f]{12,32}\b|\d+", re.IGNORECASE)
_PROMOTION_PREVIEW = re.compile(r"^(\S+ \S+ preview rejected) for listing [^,\n]+, automation [^:\n]+:")

# Set per request by the hosted access middleware: {"scope": ..., "request_id": ...}.
request_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("request_context", default=None)
_log = logging.getLogger(__name__)


def note_cause(exc: BaseException) -> None:
    """Keep the failure behind a 5xx answer for the request's error report.

    Routes turn database and network failures into a plain 503; without this
    the report says only which route answered it, not why.
    """
    context = request_context.get()
    if context is not None:
        context["cause"] = exc


def scrub(text: str, limit: int) -> str:
    """Drop SQL parameter values and mask credentials; keep the tail of long text."""
    text = _SQL_PARAMETERS.sub("[parameters: removed]", text)
    text = _SQL_STATEMENT.sub(lambda m: m.group(0)[:600] + ("…]" if len(m.group(0)) > 600 else ""), text)
    text = redact(text)
    return text if len(text) <= limit else "…" + text[-limit:]


def _request() -> dict[str, Any] | None:
    context = request_context.get()
    if not context:
        from vendoo_studio.services.service_context import current

        try:
            seller = current()
        except RuntimeError:
            seller = None
        return {"method": None, "route": "-", "request_id": None, "seller_id": seller.seller_id} if seller else None
    scope = context["scope"]
    route = scope.get("route")
    seller = (scope.get("state") or {}).get("seller")
    return {
        "method": scope.get("method"),
        "route": getattr(route, "path", None) or "-",
        "request_id": context.get("request_id"),
        "seller_id": getattr(seller, "user_id", None),
    }


def build_report(record: logging.LogRecord) -> dict[str, Any]:
    exc_type = exc_message = stack = None
    database_pool = None
    location = f"{Path(record.pathname).name}:{record.lineno} in {record.funcName}"
    identity_message = scrub(record.getMessage(), MAX_MESSAGE)
    if record.name == "vendoo_studio.listing_promotions":
        # Listing slugs and automation IDs identify attempts, not different
        # failures. Keep marketplace, action and refusal text in the identity;
        # retain the original details in the report below.
        identity_message = _PROMOTION_PREVIEW.sub(r"\1 for listing #, automation #:", identity_message)
    identity: tuple = (record.name, location, _VOLATILE.sub("#", identity_message))
    if record.exc_info and record.exc_info[0] and record.exc_info[1]:
        etype, value, tb = record.exc_info
        assert etype is not None
        from sqlalchemy.exc import TimeoutError as PoolTimeout
        if isinstance(value, PoolTimeout):
            database_pool = getattr(value, "database_pool", None)
        exc_type = f"{etype.__module__}.{etype.__qualname__}".removeprefix("builtins.")
        exc_message = scrub(str(value), MAX_MESSAGE)
        stack = scrub("".join(traceback.format_exception(etype, value, tb)), MAX_STACK)
        frames = traceback.extract_tb(tb)
        if frames:
            last = frames[-1]
            location = f"{Path(last.filename).name}:{last.lineno} in {last.name}"
        # Diagnostic plumbing must not split existing pool-error groups.
        identity_frames = [f for f in frames if not f.filename.replace("\\", "/").endswith("/vendoo_studio/database_pool.py")]
        identity = (exc_type, tuple((Path(f.filename).name, f.name) for f in identity_frames[-8:]))
    browser = getattr(record, "browser", None)
    if browser:
        # Browser stacks are minified and per-release; group by kind and message shape.
        identity = ("browser", browser["kind"], _VOLATILE.sub("#", browser["message"] or "")[:200])
        exc_type = browser.get("name") or browser["kind"]
        exc_message = scrub(browser["message"] or "", MAX_MESSAGE)
        stack = scrub(browser.get("stack") or "", MAX_STACK) or None
        location = browser.get("path") or "-"
    message = scrub(record.getMessage(), MAX_MESSAGE)
    return {
        "title": f"{exc_type or record.levelname.title()}: {(exc_message or message).splitlines()[0][:140] if (exc_message or message) else ''}",
        "severity": record.levelname,
        "occurred_at": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="seconds"),
        "fingerprint": hashlib.sha256(repr(identity).encode()).hexdigest()[:12],
        "logger": record.name,
        "message": message,
        "exception": {"type": exc_type, "message": exc_message} if exc_type else None,
        "location": location,
        "request": _request(),
        "browser": {k: browser.get(k) for k in ("kind", "path", "user_agent", "release")} if browser else None,
        "environment": {
            "service": "hosted" if os.environ.get("FLY_APP_NAME") else "local",
            "release": app_version(),
            "machine": os.environ.get("FLY_MACHINE_ID"),
            "region": os.environ.get("FLY_REGION"),
            "process_role": os.environ.get("ANTEROOM_PROCESS_ROLE", "all"),
            "process": os.getpid(),
            "thread": record.threadName,
        },
        "marketplace_context": sanitize_context(getattr(record, "marketplace_context", None)),
        "stack": stack,
        "database_pool": database_pool,
    }


def render_email(report: dict[str, Any], repeats: int, first_seen: str | None) -> tuple[str, str]:
    env = report["environment"]
    subject = f"[Anteroom {env['service']}] {report['severity']} {report['title']}"[:200]
    if repeats:
        subject += f" (+{repeats} more)"
    lines = [
        report["title"], "",
        f"Severity     {report['severity']}",
        f"When         {report['occurred_at']}",
        f"Fingerprint  {report['fingerprint']}",
    ]
    if repeats:
        lines.append(f"Occurrences  {repeats + 1} since {first_seen}")
    lines += [f"Where        {report['location']}", f"Logger       {report['logger']}"]
    if report["request"]:
        r = report["request"]
        lines += [f"Request      {r['method']} {r['route']}", f"Request id   {r['request_id']}",
                  f"Seller id    {r['seller_id'] or '-'}"]
    if report["browser"]:
        b = report["browser"]
        lines += [f"Browser      {b['kind']} on {b['path']} (web release {b['release']})", f"User agent   {b['user_agent']}"]
    lines += [f"Release      {env['release']}",
              f"Machine      {env['machine'] or '-'} ({env['region'] or '-'}, role {env['process_role']}, pid {env['process']}, {env['thread']})",
              "", "Message", "-------", report["message"] or "-"]
    if report.get("marketplace_context"):
        lines += ["", "Marketplace failure context (selected inputs; not proof of submitted values)",
                  "------------------------------------------------------------------------", json.dumps(report["marketplace_context"], sort_keys=True)]
    if report["exception"]:
        lines += ["", "Exception", "---------", f"{report['exception']['type']}: {report['exception']['message']}"]
    if report.get("database_pool"):
        lines += ["", "Database pool at timeout", "------------------------",
                  "Checkout sites and ages, not live queries. Best-effort process-local snapshot; concurrent counts may differ and connecting slots may have no holder yet.",
                  json.dumps(report["database_pool"], indent=2)]
    if report["stack"]:
        lines += ["", "Stack trace", "-----------", report["stack"]]
    lines += ["", "Structured report", "-----------------", json.dumps(report, indent=2, default=str)]
    return subject, "\n".join(lines)


def send_email(subject: str, body: str) -> None:
    """Workspace SMTP relay: allowed by the app's static egress IPs, TLS required."""
    import smtplib
    import ssl
    from email.message import EmailMessage
    from email.utils import make_msgid

    message = EmailMessage()
    message["From"] = f"Anteroom Errors <{OPERATOR}>"
    message["To"] = OPERATOR
    message["Subject"] = subject
    message["Message-ID"] = make_msgid(domain="myanteroom.com")
    message.set_content(body)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, local_hostname="myanteroom.com", timeout=20) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.send_message(message)


class AlertHandler(logging.Handler):
    """Hands records to one mail thread; emit never blocks or raises."""

    def __init__(self, send=send_email, *, store=None, daily_limit=None, clock=time.time):
        super().__init__(logging.WARNING)
        from vendoo_studio.repositories.error_email import EmailStore
        from vendoo_studio.services.error_email_delivery import EmailDelivery
        from vendoo_studio.database import engine

        self.delivery = EmailDelivery(store or EmailStore(engine), send, clock=clock,
                                      daily_limit=int(os.environ.get('ANTEROOM_ERROR_EMAIL_DAILY_LIMIT', '0'))
                                      if daily_limit is None else daily_limit)
        self.pending: queue.Queue = queue.Queue(maxsize=500)
        self.dropped = 0
        self.stopping = threading.Event()
        self.worker = threading.Thread(target=self._run, name="error-email", daemon=True)
        self.worker.start()

    def filter(self, record: logging.LogRecord) -> bool | logging.LogRecord:
        if record.name.startswith(__name__) or threading.current_thread() is self.worker:
            return False  # Never report the reporter.
        if record.name == "uvicorn.error" and str(record.msg).startswith(_UVICORN_DUPLICATE):
            return False
        return super().filter(record)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.pending.put_nowait(build_report(record))
        except queue.Full:
            self.dropped += 1
        except Exception:
            self.dropped += 1

    def _run(self) -> None:
        report = None
        while not (self.stopping.is_set() and self.pending.empty()):
            try:
                if report is None:
                    try:
                        report = self.pending.get(timeout=30)
                    except queue.Empty:
                        pass
                if report is not None:
                    self.delivery.add(report)
                    report = None
                if not self.stopping.is_set():
                    self.delivery.tick()
            except Exception as exc:
                # Retain the current report and retry storage without reporting
                # the reporter or killing its thread during a database outage.
                print(f'error-email: storage unavailable ({type(exc).__name__})', file=sys.stderr)
                if self.stopping.is_set():
                    return
                self.stopping.wait(30)

    def close(self) -> None:
        self.stopping.set()
        try:
            self.pending.put_nowait(None)
        except queue.Full:
            pass
        self.worker.join(timeout=15)
        # Never bypass the durable budget by flushing mail at shutdown.
        super().close()


_handler: AlertHandler | None = None
_previous_hooks: tuple | None = None


def _report_uncaught(exc_type, exc, tb, *, thread: str | None = None) -> None:
    logging.getLogger("vendoo_studio.uncaught").error(
        "Uncaught exception%s", f" in thread {thread}" if thread else "", exc_info=(exc_type, exc, tb))


def install_alerts() -> AlertHandler | None:
    """Enable delivery on the hosted operator only; sandbox and Mac never send."""
    global _handler, _previous_hooks
    if os.environ.get("ANTEROOM_ERROR_EMAIL") != "1" or os.environ.get("ANTEROOM_SANDBOX") == "1":
        return None
    if _handler is not None:
        return _handler
    _handler = AlertHandler()
    for name in LOGGERS:
        logging.getLogger(name).addHandler(_handler)
    previous_hook, previous_thread_hook = _previous_hooks = sys.excepthook, threading.excepthook

    def excepthook(exc_type, exc, tb):
        _report_uncaught(exc_type, exc, tb)
        previous_hook(exc_type, exc, tb)

    def thread_excepthook(args):
        if args.exc_type is not SystemExit:
            _report_uncaught(args.exc_type, args.exc_value, args.exc_traceback,
                             thread=args.thread.name if args.thread else None)
        previous_thread_hook(args)

    sys.excepthook, threading.excepthook = excepthook, thread_excepthook
    return _handler


def uninstall_alerts() -> None:
    """Flush pending reports at shutdown."""
    global _handler, _previous_hooks
    if _handler is None:
        return
    if _previous_hooks:
        sys.excepthook, threading.excepthook = _previous_hooks
        _previous_hooks = None
    for name in LOGGERS:
        logging.getLogger(name).removeHandler(_handler)
    _handler.close()
    _handler = None
