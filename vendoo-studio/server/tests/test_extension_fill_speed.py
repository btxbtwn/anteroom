from __future__ import annotations

import json
import subprocess
import unittest
from datetime import timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from vendoo_studio.services import listing_persistence
from vendoo_studio.database import Base
from vendoo_studio.models.catalog import CategorySchema  # noqa: F401
from vendoo_studio.models.conversation import utcnow
from vendoo_studio.models.fill_log import FillLogEntry
from vendoo_studio.models.registry import FieldRegistry  # noqa: F401
from vendoo_studio.repositories.queries import ConversationRepo, JobRepo
from vendoo_studio.services.job_metrics import fill_success_stats, job_timing
from extension_sources import background_source

EXTENSION = Path(__file__).resolve().parents[3] / "vendoo-extension"


def _node(script: str) -> dict:
    return json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)


class _DbTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.conv = ConversationRepo(self.db).create(title="Tee")
        revision = listing_persistence.save_revision(self.db, self.conv.id, {"title": "Tee"}, source="model")
        self.job = JobRepo(self.db).create(
            conv_id=self.conv.id, approved_revision_id=revision.id,
            listing_snapshot={"title": "Tee"}, status="completed", vendoo_item_id="item-1",
        )


class JobMetricsTest(_DbTest):
    def test_job_timing_prefers_extension_durations(self):
        repo = JobRepo(self.db)
        repo.add_event(self.job.id, "step_completed", "filling_general", {"duration_ms": 4200})
        repo.add_event(self.job.id, "step_completed", "saving_ebay", {"duration_ms": 10, "skipped": True})
        repo.add_event(self.job.id, "step_failed", "filling_etsy", {"error": "x"})
        timing = job_timing(self.db, self.job.id)
        self.assertEqual([step["step"] for step in timing["steps"]], ["filling_general", "saving_ebay", "filling_etsy"])
        self.assertEqual(timing["steps"][0]["duration_ms"], 4200)
        self.assertTrue(timing["steps"][1]["skipped"])
        self.assertEqual(timing["steps"][2]["source"], "event_gap")
        self.assertEqual(timing["slowest"][0]["step"], "filling_general")

    def test_fill_stats_rank_worst_fields_first(self):
        now = utcnow()
        rows = [
            ("ebay", "Type", "filled"), ("ebay", "Type", "filled"),
            ("etsy", "Sleeve length", "invalid"), ("etsy", "Sleeve length", "filled"),
            ("etsy", "Holiday", "skipped"),
        ]
        for marketplace, field, status in rows:
            self.db.add(FillLogEntry(job_id=self.job.id, conversation_id=self.conv.id, step="filling_fields",
                                     marketplace=marketplace, field=field, status=status, created_at=now))
        self.db.add(FillLogEntry(job_id=self.job.id, conversation_id=self.conv.id, step="old",
                                 marketplace="ebay", field="Type", status="invalid",
                                 created_at=now - timedelta(days=90)))
        self.db.commit()
        stats = fill_success_stats(self.db, days=30)
        self.assertEqual(stats["entries"], 5)
        self.assertEqual(stats["fields"][0]["field"], "Sleeve length")
        self.assertEqual(stats["fields"][0]["success_rate"], 0.5)
        self.assertEqual(stats["by_marketplace"]["ebay"]["success_rate"], 1.0)
