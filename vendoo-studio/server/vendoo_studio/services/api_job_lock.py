"""Which direct marketplace jobs are running, and where.

A direct action holds its job in ``dispatched`` for the whole request. The
process running it registers the job here, so the job watchdog
(``services/job_watchdog.py``) can tell a running job from one a restart left
behind, and a Cancel reaches the job wherever it runs.

On the Mac one process owns every job: this memory is the whole answer and
nothing more is written. Where several Machines share one database (Fly sets
``FLY_MACHINE_ID``, the same switch as ``device_routes``), the job row also
carries a lease, in the shape of ``background_tasks``: the owning Machine and
a ``leased_until`` the owner renews every ``RENEW_EVERY_SEC`` while the job
runs. Another Machine counts the job as running until that lease lapses.
Cancel is the row's ``cancel_requested`` flag, which the owner reads back with
each renewal and its job checks at its existing cancellation points.

A lapsed lease only lets the watchdog mark the job interrupted, its result
unconfirmed. Nothing here starts, retries or re-queues a marketplace action.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import CursorResult, or_, select, update
from sqlalchemy.orm import Session

from vendoo_studio.models.job import ACTIVE_JOB_STATUSES, RUNNING_JOB_STATUSES, Job

log = logging.getLogger(__name__)

LEASE = timedelta(seconds=30)
RENEW_EVERY_SEC = 5.0

# Check-and-claim for a new job. Held only across the busy check and the
# insert, never across the Chrome or marketplace work.
_start_gate = threading.Lock()


def start_gate() -> threading.Lock:
    return _start_gate


# Lease columns are compared in the database; the session's copies are refreshed after commit.
_UPDATE_JOB = update(Job).execution_options(synchronize_session=False)


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class JobLeases:
    """One process's running jobs. ``owner`` is None where this process is the only one."""

    def __init__(self, owner: str | None, *, heartbeat: bool = True):
        self.owner = owner or None
        self._heartbeat = heartbeat
        # job id -> the seller context it runs in (hosted renewals open that seller's session).
        self._live: dict[str, object | None] = {}
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def claim(self, db: Session, job_id: str) -> None:
        from vendoo_studio.services.service_context import current

        with self._lock:
            self._live[job_id] = current() if self.owner else None
            self._cancelled.discard(job_id)
        if self.owner is None:
            return
        # updated_at stays: the watchdog reads it as the job's own progress.
        db.execute(_UPDATE_JOB.where(Job.id == job_id).values(
            lease_owner=self.owner, leased_until=_now() + LEASE, updated_at=Job.updated_at))
        db.commit()
        self._start_heartbeat()

    def release(self, job_id: str) -> None:
        with self._lock:
            self._live.pop(job_id, None)
            self._cancelled.discard(job_id)

    def holds(self, job: Job, now: datetime | None = None) -> bool:
        """Whether this process, or another one's unexpired lease, is running ``job``."""
        return job.id in self._live or self._held_elsewhere(job.status, job.lease_owner, job.leased_until, now)

    def _held_elsewhere(self, status: str | None, owner: str | None, until: datetime | None,
                        now: datetime | None = None) -> bool:
        if status not in ACTIVE_JOB_STATUSES or not owner or owner == self.owner:
            # One server per Machine: a lease this Machine wrote but no longer
            # holds belongs to a process that has stopped.
            return False
        until = _as_utc(until)
        return until is not None and until > (now or _now())

    def is_running(self, db: Session, job_id: str) -> bool:
        if job_id in self._live:
            return True
        if self.owner is None:
            # The only process: its memory is the whole answer, with no database read.
            return False
        row = db.execute(select(Job.status, Job.lease_owner, Job.leased_until).where(Job.id == job_id)).first()
        return row is not None and self._held_elsewhere(*row)

    def interrupt(self, db: Session, job_id: str, error: str, now: datetime) -> bool:
        """Fail a running job no process holds. False when its owner renewed it first."""
        if job_id in self._live:
            return False
        unheld = or_(Job.lease_owner.is_(None), Job.leased_until.is_(None), Job.leased_until <= now)
        if self.owner is not None:
            unheld = or_(unheld, Job.lease_owner == self.owner)
        changed = cast(CursorResult, db.execute(_UPDATE_JOB.where(
            Job.id == job_id, Job.status.in_(RUNNING_JOB_STATUSES), unheld,
        ).values(status="failed", last_error=error))).rowcount
        db.commit()
        return changed == 1

    def request_cancel(self, db: Session, job_id: str) -> bool:
        """Ask the job to stop at its next safe point. True the first time it is asked."""
        with self._lock:
            if job_id in self._live:
                self._cancelled.add(job_id)
        changed = cast(CursorResult, db.execute(_UPDATE_JOB.where(
            Job.id == job_id, Job.status.in_(ACTIVE_JOB_STATUSES), Job.cancel_requested.is_not(True),
        ).values(cancel_requested=True, updated_at=Job.updated_at))).rowcount
        db.commit()
        return changed == 1

    def cancel_requested(self, job_id: str) -> bool:
        """Local and fast: checked at every cancellation point while the job waits."""
        return job_id in self._cancelled

    def renew(self) -> None:
        """Extend this process's leases and pick up Cancels requested on other Machines."""
        if self.owner is None:
            return
        from vendoo_studio.database import SessionLocal
        from vendoo_studio.services.service_context import use

        with self._lock:
            groups: dict[str | None, tuple[object | None, list[str]]] = {}
            for job_id, context in self._live.items():
                key = getattr(context, "seller_id", None)
                groups.setdefault(key, (context, []))[1].append(job_id)
        for context, job_ids in groups.values():
            mine = (Job.id.in_(job_ids), Job.lease_owner == self.owner, Job.status.in_(ACTIVE_JOB_STATUSES))
            with use(context) if context is not None else nullcontext():  # type: ignore[arg-type]
                with SessionLocal() as db:
                    # Lease metadata is not a listing edit; open editors need not refetch.
                    db.info["quiet_changes"] = True
                    db.execute(_UPDATE_JOB.where(*mine).values(leased_until=_now() + LEASE, updated_at=Job.updated_at))
                    rows = db.execute(select(Job.id, Job.cancel_requested).where(*mine))
                    held = {job_id: bool(flag) for job_id, flag in rows}
                    db.commit()
            with self._lock:
                for job_id in job_ids:
                    # A job no longer held here was cancelled, or failed by a watchdog
                    # after its lease lapsed: stop it at its next safe point.
                    if job_id in self._live and held.get(job_id, True):
                        self._cancelled.add(job_id)

    def _start_heartbeat(self) -> None:
        if not self._heartbeat:
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._beat, name="anteroom-job-leases", daemon=True)
            self._thread.start()

    def _beat(self) -> None:
        # A thread, not a task: jobs run on several event loops, and a slow
        # database must not hold any of them.
        while True:
            time.sleep(RENEW_EVERY_SEC)
            with self._lock:
                if not self._live:
                    self._thread = None
                    return
            try:
                self.renew()
            except Exception:  # noqa: BLE001 - the next beat tries again before the lease lapses
                log.warning("Could not renew running job leases", exc_info=True)


def _process_owner() -> str | None:
    from vendoo_studio.services.device_routes import machine_id

    return machine_id() or None


_process = JobLeases(_process_owner())


def process_leases() -> JobLeases:
    return _process


def claim_api_job(db: Session, job_id: str) -> None:
    _process.claim(db, job_id)


def release_api_job(job_id: str) -> None:
    _process.release(job_id)


def job_is_live(db: Session, job_id: str) -> bool:
    return _process.is_running(db, job_id)


def request_job_cancel(db: Session, job_id: str) -> bool:
    return _process.request_cancel(db, job_id)


def job_cancel_requested(job_id: str) -> bool:
    return _process.cancel_requested(job_id)
