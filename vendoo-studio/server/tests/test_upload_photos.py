from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.services import listing_persistence
from vendoo_studio.services.photo_store import LocalPhotoStore
from vendoo_studio.database import Base, get_db
from vendoo_studio.main import app
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.job import Job
from vendoo_studio.models.listing import Listing, ListingRevision  # noqa: F401
from vendoo_studio.models.registry import FieldRegistry  # noqa: F401

JPEG_BYTES = bytes([
    0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01,
    0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0xFF, 0xD9,
])


class JobPhotoRouteTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        self.db = Session()
        self.conv = Conversation(title="Bamboo jeans")
        self.db.add(self.conv)
        self.db.commit()
        self.job = Job(
            conversation_id=self.conv.id,
            approved_revision_id="rev1",
            listing_snapshot={"title": "Bamboo jeans"},
            status="dispatched",
        )
        self.db.add(self.job)
        self.db.commit()
        self.tmp = tempfile.TemporaryDirectory()
        stored = "front.jpg"
        (Path(self.tmp.name) / stored).write_bytes(JPEG_BYTES)
        self.photo = listing_persistence.add_photo(self.db,
            self.conv.id,
            "front.jpg",
            stored,
            "image/jpeg",
            len(JPEG_BYTES),
        )

        def override_get_db():
            try:
                yield self.db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()
        self.tmp.cleanup()

    def test_job_photo_is_served_for_matching_conversation(self):
        with patch("vendoo_studio.services.photo_store._store", LocalPhotoStore(self.tmp.name)):
            response = self.client.get(f"/api/jobs/{self.job.id}/photos/{self.photo.id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, JPEG_BYTES)
        self.assertIn("image/jpeg", response.headers.get("content-type", ""))

    def test_job_photo_rejects_unknown_photo(self):
        with patch("vendoo_studio.services.photo_store._store", LocalPhotoStore(self.tmp.name)):
            response = self.client.get(f"/api/jobs/{self.job.id}/photos/does-not-exist")
        self.assertEqual(response.status_code, 404)
