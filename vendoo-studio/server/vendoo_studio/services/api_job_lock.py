"""Which direct marketplace jobs are actually running in this process.

A direct action holds its job in ``dispatched`` for the whole request. Live
jobs register here, so the job watchdog (``services/job_watchdog.py``) can tell
a job this process is running from one a restart left behind.
"""

from __future__ import annotations

import threading

_live_api_job_ids: set[str] = set()
# Check-and-claim for a new job. Held only across the busy check and the
# insert, never across the Chrome or marketplace work.
_start_gate = threading.Lock()


def start_gate() -> threading.Lock:
    return _start_gate


def claim_api_job(job_id: str) -> None:
    _live_api_job_ids.add(job_id)


def release_api_job(job_id: str) -> None:
    _live_api_job_ids.discard(job_id)


def api_job_is_live(job_id: str) -> bool:
    return job_id in _live_api_job_ids
