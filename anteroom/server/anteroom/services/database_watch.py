"""Tell the operator when the hosted database stays unreachable, and when it is back.

``GET /api/ready`` answers 503 while Postgres refuses connections, but nothing
calls it: Fly routes on ``/api/health``, which deliberately never touches the
database. During an outage the only signal used to be a flood of unrelated
error emails. This watcher runs the same check as ``/api/ready`` every 30
seconds on the web process and, after two minutes of continuous failure, sends
one "Hosted database unreachable" email, then one "reachable again" email when
the check passes.

The shared daily mail budget lives in the database, so it cannot be read during
an outage. The watcher charges the budget when it can, and sends the outage
email anyway when it cannot: at most one per outage. A failed or refused send
is tried again later. It logs under ``error_alerts`` so its own messages never
become error emails.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

CHECK_INTERVAL = 30
# Postgres connects have no connect timeout of their own.
CHECK_DEADLINE = 10
ALERT_AFTER = 120
RETRY_AFTER = 300

OUTAGE, RECOVERED = "outage", "recovered"

log = logging.getLogger("anteroom.services.error_alerts.database_watch")


def readiness(engine, expected_revision: Callable[[], str | None]) -> tuple[str, BaseException | None]:
    """``("ok" | "unavailable" | "schema_mismatch", error)``: what ``/api/ready`` answers."""
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    if engine.dialect.name != "postgresql":
        return "ok", None
    try:
        with engine.connect() as connection, connection.begin():
            # LOCAL so a reused connection does not keep the one-second limit.
            connection.execute(text("SET LOCAL statement_timeout = '1000'"))
            revision = connection.scalar(text("SELECT anteroom_private.schema_revision()"))
    except SQLAlchemyError as exc:
        return "unavailable", exc
    if revision != expected_revision():
        return "schema_mismatch", None
    return "ok", None


def _minutes(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


def _where() -> str:
    return (f"Machine {os.environ.get('FLY_MACHINE_ID') or '-'} ({os.environ.get('FLY_REGION') or '-'}), "
            f"app {os.environ.get('FLY_APP_NAME') or '-'}")


class DatabaseWatch:
    """Checks readiness on a timer and sends one email per outage and one per recovery."""

    def __init__(
        self,
        check: Callable[[], str],
        send: Callable[[str, str], None],
        budget: Callable[[], bool | None],
        *,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        alert_after: float = ALERT_AFTER,
        retry_after: float = RETRY_AFTER,
        check_deadline: float = CHECK_DEADLINE,
    ):
        """``budget`` returns True to send, False to wait, or None when it cannot be read."""
        self.check, self.send, self.budget = check, send, budget
        self.clock, self.wall = clock, wall
        self.alert_after, self.retry_after, self.check_deadline = alert_after, retry_after, check_deadline
        self.failing_since: float | None = None
        self.failing_since_wall: float | None = None
        self.status = "ok"
        # Whether the operator was told about the current outage.
        self.alerted = False
        self.pending: str | None = None
        self.retry_at = 0.0
        self.outage_seconds = 0.0
        self._running: dict[str, threading.Thread] = {}

    def _within_deadline[T](self, name: str, fn: Callable[[], T], failed: T) -> T:
        """``fn()`` within the deadline; ``failed`` when it raises, times out or is still running."""
        running = self._running.get(name)
        if running is not None and running.is_alive():
            return failed
        result: list[T] = []

        def run():
            try:
                result.append(fn())
            except Exception:  # noqa: BLE001 - any failure to answer counts as failed
                result.append(failed)

        thread = threading.Thread(target=run, name=f"database-watch-{name}", daemon=True)
        self._running[name] = thread
        thread.start()
        thread.join(self.check_deadline)
        return result[0] if result else failed

    def _check(self) -> str:
        """The check's answer within its deadline; one still running counts as a failure."""
        return self._within_deadline("check", self.check, "unavailable")

    def tick(self) -> None:
        status = self._check()
        now = self.clock()
        if status != "ok":
            self.status = status
            if self.failing_since is None:
                self.failing_since, self.failing_since_wall = now, self.wall()
                log.warning("Hosted database check failed (%s)", status)
            if self.pending == RECOVERED:
                # Back down before the recovery went out: the same outage goes on.
                self.pending = None
            if not self.alerted and self.pending is None and now - self.failing_since >= self.alert_after:
                self.pending, self.retry_at = OUTAGE, now
        elif self.failing_since is not None:
            self.outage_seconds = now - self.failing_since
            self.failing_since = None
            log.warning("Hosted database check passes again after %s", _minutes(self.outage_seconds))
            if self.pending == OUTAGE:
                self.pending = None  # Never announced, so nothing to take back.
            elif self.alerted:
                self.pending, self.retry_at = RECOVERED, now
        if self.pending is not None and now >= self.retry_at:
            self._deliver(now)

    def _message(self) -> tuple[str, str]:
        started = datetime.fromtimestamp(self.failing_since_wall or self.wall(), UTC).strftime("%Y-%m-%d %H:%M UTC")
        if self.pending == OUTAGE:
            reason = ("Postgres is not accepting connections" if self.status == "unavailable"
                      else "Postgres answers, but at a schema revision this release was not built for")
            failing = _minutes(self.clock() - (self.failing_since or self.clock()))
            return ("[Anteroom hosted] Hosted database unreachable", "\n".join([
                "Hosted database unreachable", "",
                f"GET /api/ready has failed for {failing}, since {started} ({self.status}): {reason}.",
                "Sellers see \"Can't reach your data right now\" and nothing loads or saves until it passes.",
                "/api/health stays green on purpose, so Fly neither restarts nor alerts.", "",
                "Check the Supabase project's status, compute size and connection count first",
                "(docs/hosted-production.md). You will get one more email when it is reachable again.", "",
                _where(),
            ]))
        return ("[Anteroom hosted] Hosted database reachable again", "\n".join([
            "Hosted database reachable again", "",
            f"GET /api/ready passes again after failing for about {_minutes(self.outage_seconds)} (since {started}).",
            "", _where(),
        ]))

    def _deliver(self, now: float) -> None:
        # The budget lives on the same engine as the check, so it can hang the same way.
        allowed: bool | None = self._within_deadline("budget", self.budget, None)
        if allowed is None:
            # The budget lives in the unreachable database; an outage email still goes, once.
            allowed = self.pending == OUTAGE
        if not allowed:
            self.retry_at = now + self.retry_after
            log.warning("Hosted database %s email waits for the mail budget", self.pending)
            return
        subject, body = self._message()
        try:
            self.send(subject, body)
        except Exception as exc:  # noqa: BLE001 - tried again later
            self.retry_at = now + self.retry_after
            log.warning("Hosted database %s email failed (%s); trying again later", self.pending, type(exc).__name__)
            return
        self.alerted = self.pending == OUTAGE
        log.warning("Hosted database %s email sent", self.pending)
        self.pending = None

    def run(self, stopping: threading.Event, interval: float = CHECK_INTERVAL) -> None:
        while not stopping.wait(interval):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - the watcher must outlive any one check
                log.warning("Hosted database watch failed a round", exc_info=True)


def _budget(engine) -> Callable[[], bool | None]:
    from anteroom.repositories.error_email import EmailStore
    from anteroom.services.error_email_delivery import reserve_direct_send

    store = EmailStore(engine)
    try:
        daily_limit = int(os.environ.get("ANTEROOM_ERROR_EMAIL_DAILY_LIMIT", "0"))
    except ValueError:
        daily_limit = 0

    def budget() -> bool | None:
        if daily_limit <= 0:
            return False  # The operator turned operator mail off.
        try:
            return reserve_direct_send(store, daily_limit, keep_error_reserve=False)
        except Exception:  # noqa: BLE001 - the database holding the budget is down
            return None
    return budget


def start(engine, expected_revision: Callable[[], str | None]) -> Callable[[], None]:
    """Watch from a daemon thread; returns what stops it."""
    from anteroom.services.error_alerts import send_email

    watch = DatabaseWatch(
        lambda: readiness(engine, expected_revision)[0],
        lambda subject, body: send_email(subject, body, sender="Anteroom Alerts"),
        _budget(engine),
    )
    stopping = threading.Event()
    thread = threading.Thread(target=watch.run, args=(stopping,), name="database-watch", daemon=True)
    thread.start()

    def stop() -> None:
        stopping.set()
        # A daemon thread with nothing to save; a send in flight is not waited out.
        thread.join(timeout=5)
    return stop
