"""Email the operator a structured report for every distinct hosted error.

Every ERROR record from Anteroom's loggers, uvicorn, asyncio and uncaught
thread or process exceptions becomes one report: what failed (exception type
and message), where (stack and call site), when, on which release and Machine,
for which request (method, route template, request id) and seller id. Browser
crashes arrive through ``routes/error_reports.py`` and are reported the same way.

Reports group by fingerprint (exception type and code location, or call site
when there is no exception). The first of a group is emailed straight away;
repeats within ``REPEAT_WINDOW`` are counted and summarised in the next email
for that group. A global hourly cap keeps a crash loop from flooding the inbox.

Messages and stacks pass through ``redaction.redact``; SQL parameters are
dropped. Delivery runs on its own thread with retries, never on a request.
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

from vendoo_studio.services.redaction import redact
from vendoo_studio.version import app_version

OPERATOR = "ccanizares@myanteroom.com"
SMTP_HOST, SMTP_PORT = "smtp-relay.gmail.com", 587
REPEAT_WINDOW = 15 * 60
HOURLY_CAP = 60
RETRY_DELAYS = (5, 30, 120)
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
_VOLATILE = re.compile(r"0x[0-9a-f]+|\b[0-9a-f]{8}-[0-9a-f-]{27}\b|\d+", re.IGNORECASE)

# Set per request by the hosted access middleware: {"scope": ..., "request_id": ...}.
request_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("request_context", default=None)
_log = logging.getLogger(__name__)


def scrub(text: str, limit: int) -> str:
    """Drop SQL parameter values and mask credentials; keep the tail of long text."""
    text = _SQL_PARAMETERS.sub("[parameters: removed]", text)
    text = _SQL_STATEMENT.sub(lambda m: m.group(0)[:600] + ("…]" if len(m.group(0)) > 600 else ""), text)
    text = redact(text)
    return text if len(text) <= limit else "…" + text[-limit:]


def _request() -> dict[str, Any] | None:
    context = request_context.get()
    if not context:
        return None
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
    location = f"{Path(record.pathname).name}:{record.lineno} in {record.funcName}"
    identity: tuple = (record.name, location)
    if record.exc_info and record.exc_info[0] and record.exc_info[1]:
        etype, value, tb = record.exc_info
        assert etype is not None
        exc_type = f"{etype.__module__}.{etype.__qualname__}".removeprefix("builtins.")
        exc_message = scrub(str(value), MAX_MESSAGE)
        stack = scrub("".join(traceback.format_exception(etype, value, tb)), MAX_STACK)
        frames = traceback.extract_tb(tb)
        if frames:
            last = frames[-1]
            location = f"{Path(last.filename).name}:{last.lineno} in {last.name}"
        identity = (exc_type, tuple((Path(f.filename).name, f.name) for f in frames[-8:]))
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
        "stack": stack,
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
        lines.append(f"Occurrences  {repeats + 1} since {first_seen} (repeats are grouped for {REPEAT_WINDOW // 60} min)")
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
    if report["exception"]:
        lines += ["", "Exception", "---------", f"{report['exception']['type']}: {report['exception']['message']}"]
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

    def __init__(self, send=send_email, *, sleep=time.sleep, clock=time.monotonic):
        super().__init__(logging.ERROR)
        self.send, self.sleep, self.clock = send, sleep, clock
        self.pending: queue.Queue = queue.Queue(maxsize=500)
        self.dropped = 0
        self.groups: dict[str, dict[str, Any]] = {}
        self.sent_at: list[float] = []
        self.capped = 0
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
        while not (self.stopping.is_set() and self.pending.empty()):
            try:
                report = self.pending.get(timeout=0.5)
            except queue.Empty:
                continue
            self.process(report)

    def process(self, report: dict[str, Any]) -> None:
        now = self.clock()
        group = self.groups.get(report["fingerprint"])
        if group and now - group["sent"] < REPEAT_WINDOW:
            group["repeats"] += 1
            group["latest"] = report
            return
        repeats = group["repeats"] if group else 0
        first_seen = group["first_seen"] if group else None
        self.groups[report["fingerprint"]] = {"sent": now, "repeats": 0, "first_seen": report["occurred_at"], "latest": None}
        self.sent_at = [t for t in self.sent_at if now - t < 3600]
        if len(self.sent_at) >= HOURLY_CAP:
            self.capped += 1
            return
        self.sent_at.append(now)
        subject, body = render_email(report, repeats, first_seen)
        if self.capped or self.dropped:
            body = (f"Note: {self.capped} report(s) were not emailed because of the {HOURLY_CAP}/hour cap and "
                    f"{self.dropped} were dropped by a full queue since the last email. "
                    "Check `fly logs` for them.\n\n") + body
            self.capped = self.dropped = 0
        self.deliver(subject, body)

    def deliver(self, subject: str, body: str) -> bool:
        for attempt, delay in enumerate((0, *RETRY_DELAYS)):
            if delay:
                if self.stopping.is_set():
                    break
                self.sleep(delay)
            try:
                self.send(subject, body)
                return True
            except Exception as exc:
                # stderr, not logging: a mail outage must not report itself.
                print(f"error-email: attempt {attempt + 1} failed ({type(exc).__name__})", file=sys.stderr)
        print(f"error-email: gave up on '{subject}'", file=sys.stderr)
        return False

    def flush_repeats(self) -> None:
        """At shutdown, email groups whose repeats have not been reported yet."""
        for group in self.groups.values():
            if group["repeats"] and group["latest"]:
                self.deliver(*render_email(group["latest"], group["repeats"], group["first_seen"]))
                group["repeats"] = 0

    def close(self) -> None:
        self.stopping.set()
        self.worker.join(timeout=15)
        try:
            self.flush_repeats()
        finally:
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
