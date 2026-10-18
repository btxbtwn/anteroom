"""Which Vendoo API sends are actually in this process.

Create and save hold a job in ``dispatched`` for the whole request, including
the model step that fills marketplace fields. That step does not touch Chrome.
Live sends register here, so the job watchdog (``services/job_watchdog.py``)
can tell a send this process is running from one a restart left behind.
"""

from __future__ import annotations

import threading

from sqlalchemy.orm import Session

from vendoo_studio.models.job import ABANDONED_SEND, is_vendoo_api_step
from vendoo_studio.repositories.queries import JobRepo

_live_api_job_ids: set[str] = set()
# Check-and-claim for a new send. Held only across the busy check and the
# insert, never across the Chrome or model work.
_start_gate = threading.Lock()


def start_gate() -> threading.Lock:
    return _start_gate


def claim_api_job(job_id: str) -> None:
    _live_api_job_ids.add(job_id)


def release_api_job(job_id: str) -> None:
    _live_api_job_ids.discard(job_id)


def api_job_is_live(job_id: str) -> bool:
    return job_id in _live_api_job_ids


def abandon_api_job_if_still_running(db: Session, job_id: str) -> None:
    """Fail a send whose request ended without completing or cancelling it."""
    repo = JobRepo(db)
    job = repo.get(job_id)
    if not job or str(job.status or "") != "dispatched":
        return
    if not is_vendoo_api_step(job.current_step):
        return
    repo.update_status(job.id, "failed", job.current_step, error=ABANDONED_SEND)
    repo.add_event(job.id, "vendoo_api_abandoned", job.current_step, {"reason": "request_ended"})
