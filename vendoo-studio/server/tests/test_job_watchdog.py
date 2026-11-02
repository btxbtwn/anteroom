"""Direct jobs release interrupted slots without retrying a marketplace action."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy.orm import sessionmaker

from vendoo_studio.services import listing_persistence
from vendoo_studio.database import Base, make_engine
from vendoo_studio.repositories.queries import ConversationRepo, JobRepo
from vendoo_studio.services.extension_connection import ExtensionManager
from vendoo_studio.services.api_job_lock import claim_api_job, release_api_job
from vendoo_studio.services.job_watchdog import STALL_AFTER, STALLED_JOB, fail_stalled_jobs


class _ConnectedManager(ExtensionManager):
    """A real wait registry with Chrome reported as connected."""

    connected = True


def _sessions(test):
    # The watchdog and dispatcher open sessions on worker threads. StaticPool
    # would hand them the test session's live SQLite connection, allowing
    # concurrent cursor/transaction use and occasionally crashing the worker.
    directory = TemporaryDirectory(prefix="watchdog-test-")
    test.addCleanup(directory.cleanup)
    engine = make_engine(f"sqlite:///{Path(directory.name) / 'watchdog.db'}")
    test.addCleanup(engine.dispose)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


class FailStalledJobsTest(unittest.TestCase):
    def setUp(self):
        self.db = _sessions(self)()
        self.manager = _ConnectedManager()
        p = patch("vendoo_studio.services.extension_connection.extension_manager", self.manager)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.db.close)
        self.live: list[str] = []
        self.addCleanup(lambda: [release_api_job(job_id) for job_id in self.live])

    def job(self, *, status="dispatched", step="native_poshmark_save_draft", live=False):
        repo = ConversationRepo(self.db)
        conv = repo.create(title="Tee")
        rev = listing_persistence.save_revision(self.db, conv.id, {"title": "Tee"}, source="model")
        job = JobRepo(self.db).create(conv.id, rev.id, {"title": "Tee"}, status=status, current_step=step)
        if live:
            claim_api_job(job.id)
            self.live.append(job.id)
        return job

    def later(self, after: timedelta) -> datetime:
        return datetime.now(UTC) + after

    def status(self, job):
        self.db.expire_all()
        return JobRepo(self.db).get(job.id)

    def test_live_direct_job_keeps_its_slot_even_after_the_silence_threshold(self):
        job = self.job(live=True)
        self.assertEqual(fail_stalled_jobs(self.db, now=self.later(STALL_AFTER * 2)), [])
        self.assertEqual(self.status(job).status, "dispatched")

    def test_interrupted_direct_job_fails_without_being_requeued(self):
        job = self.job()
        with patch("vendoo_studio.services.native_marketplaces.mark_interrupted"), \
                patch("vendoo_studio.services.listing_promotions.mark_interrupted"):
            self.assertEqual(fail_stalled_jobs(self.db, now=self.later(timedelta(seconds=6))), [job.id])
        self.assertEqual(self.status(job).status, "failed")
        events = JobRepo(self.db).get_events(job.id)
        self.assertEqual([event.event_type for event in events], ["watchdog_failed"])
        self.assertEqual(events[0].payload["reason"], "native_interrupted")
        self.assertEqual(fail_stalled_jobs(self.db, now=self.later(STALL_AFTER * 2)), [])

    def test_a_new_job_has_time_to_be_claimed_by_its_worker(self):
        job = self.job()
        self.assertEqual(fail_stalled_jobs(self.db, now=self.later(timedelta(seconds=1))), [])
        self.assertEqual(self.status(job).status, "dispatched")

    def test_waiting_and_terminal_jobs_are_not_changed_or_dispatched(self):
        jobs = [self.job(status=status) for status in
                ("queued", "awaiting_extension", "completed", "cancelled", "failed")]
        self.assertEqual(fail_stalled_jobs(self.db, now=self.later(STALL_AFTER * 2)), [])
        self.assertEqual([self.status(job).status for job in jobs],
                         ["queued", "awaiting_extension", "completed", "cancelled", "failed"])

    def test_old_unrecognized_jobs_fail_once_without_retry(self):
        job = self.job(step="historical_step")
        self.assertEqual(fail_stalled_jobs(self.db, now=self.later(STALL_AFTER * 2)), [job.id])
        self.assertEqual(self.status(job).status, "failed")
        self.assertEqual(self.status(job).last_error, STALLED_JOB)
        self.assertEqual(fail_stalled_jobs(self.db, now=self.later(STALL_AFTER * 3)), [])
