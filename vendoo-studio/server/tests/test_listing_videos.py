from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, get_db
from vendoo_studio.main import app
from vendoo_studio.models.conversation import Photo
from vendoo_studio.models.listing_video import ListingVideo
from vendoo_studio.repositories.queries import ConversationRepo, JobRepo, ListingRepo
from vendoo_studio.services import listing_videos
from vendoo_studio.services.automations import approval_snapshot

CLIP = (Path(__file__).parent / "fixtures" / "listing-video.mp4").read_bytes()


class ListingVideosTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)
        self.conv = ConversationRepo(self.db).create(title="Private video test")
        self.revision = ListingRepo(self.db).save_revision(self.conv.id, {"title": "Private video test"}, "user_form")
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(listing_videos, "VIDEOS_DIR", Path(self.directory)))
        self.base = f"/api/conversations/{self.conv.id}/videos"

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def upload(self):
        response = self.client.post(self.base, files={"file": ("clip.mp4", CLIP, "video/mp4")})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_upload_reads_actual_video_metadata_and_does_not_add_a_photo(self):
        video = self.upload()
        self.assertEqual((video["duration_seconds"], video["width"], video["height"]), (1, 16, 16))
        self.assertEqual(video["mime_type"], "video/mp4")
        self.assertEqual(self.db.query(Photo).filter_by(conversation_id=self.conv.id).count(), 0)
        self.assertEqual(self.client.get(self.base).json()["videos"], [video])
        response = self.client.get(video["url"], headers={"Range": "bytes=0-15"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, CLIP[:16])

    def test_invalid_and_oversize_uploads_leave_no_file_or_record(self):
        response = self.client.post(self.base, files={"file": ("fake.mp4", b"not a video", "video/mp4")})
        self.assertEqual(response.status_code, 400)
        with patch.object(listing_videos, "MAX_VIDEO_BYTES", 32):
            response = self.client.post(self.base, files={"file": ("clip.mp4", CLIP, "video/mp4")})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(Path(self.directory).iterdir()), [])
        self.assertEqual(self.db.query(ListingVideo).count(), 0)

    def test_damaged_video_and_parser_failures_are_readable_refusals(self):
        response = self.client.post(self.base, files={"file": ("truncated.mp4", CLIP[:100], "video/mp4")})
        self.assertEqual(response.status_code, 400)
        with patch.object(listing_videos, "extractMetadata", side_effect=RuntimeError("damaged media")):
            response = self.client.post(self.base, files={"file": ("clip.mp4", CLIP, "video/mp4")})
        self.assertEqual(response.status_code, 400)
        self.assertIn("could not be read", response.json()["detail"])
        self.assertEqual(list(Path(self.directory).iterdir()), [])
        self.assertEqual(self.db.query(ListingVideo).count(), 0)

    def test_delivery_requires_the_jobs_selected_video_checksum_and_owner(self):
        video = self.upload()
        selected = listing_videos.selected_video(self.db, self.conv.id, {"poshmark_specifics": {"videoId": video["id"]}}, "poshmark")
        job = JobRepo(self.db).create(self.conv.id, self.revision.id, {"_native_video": selected}, status="completed")
        endpoint = f"/api/jobs/{job.id}/videos/{video['id']}"
        self.assertEqual(self.client.get(endpoint).content, CLIP)
        job.listing_snapshot = {"_native_video": {**selected, "checksum": "different"}}
        self.db.commit()
        self.assertEqual(self.client.get(endpoint).status_code, 404)
        other = ConversationRepo(self.db).create(title="Another item")
        job.conversation_id = other.id
        job.listing_snapshot = {"_native_video": selected}
        self.db.commit()
        self.assertEqual(self.client.get(endpoint).status_code, 404)
        with self.assertRaisesRegex(ValueError, "missing"):
            listing_videos.selected_video(self.db, other.id, {"poshmark_specifics": {"videoId": video["id"]}}, "poshmark")

    def test_poshmark_duration_and_selected_or_busy_deletion_are_blocked(self):
        video = self.upload()
        row = self.db.get(ListingVideo, video["id"])
        row.duration_seconds = 15
        self.db.commit()
        listing = {"poshmark_specifics": {"videoId": video["id"]}}
        with self.assertRaisesRegex(ValueError, "shorter than 15"):
            listing_videos.selected_video(self.db, self.conv.id, listing, "poshmark")
        ListingRepo(self.db).save_revision(self.conv.id, listing, "user_form")
        endpoint = f"{self.base}/{video['id']}"
        self.assertEqual(self.client.delete(endpoint).status_code, 409)
        ListingRepo(self.db).save_revision(self.conv.id, {"poshmark_specifics": {"videoId": ""}}, "user_form")
        job = JobRepo(self.db).create(self.conv.id, self.revision.id, {}, status="dispatched")
        self.assertEqual(self.client.delete(endpoint).status_code, 409)
        job.status = "completed"
        self.db.commit()
        self.assertEqual(self.client.delete(endpoint).status_code, 200)
        self.assertFalse(listing_videos.video_path(row).exists())

    def test_ebay_and_etsy_selection_check_their_own_limits_and_carry_the_file(self):
        video = self.upload()
        selected = listing_videos.selected_video(self.db, self.conv.id, {"ebay_specifics": {"videoId": video["id"]}}, "ebay")
        self.assertEqual(listing_videos.video_bytes(selected), CLIP)
        with self.assertRaisesRegex(ValueError, "3 to 15 seconds"):
            listing_videos.selected_video(self.db, self.conv.id, {"etsy_specifics": {"videoId": video["id"]}}, "etsy")
        self.assertIsNone(listing_videos.selected_video(self.db, self.conv.id, {"etsy_specifics": {"videoId": ""}}, "etsy"))
        self.assertIn("ebay", self.client.get(self.base).json()["rules"])
        with self.assertRaisesRegex(ValueError, "changed"):
            listing_videos.video_bytes({**selected, "checksum": "different"})

    def test_new_video_invalidates_an_automation_review(self):
        before, _ = approval_snapshot(self.db, self.conv.id, [])
        self.upload()
        after, _ = approval_snapshot(self.db, self.conv.id, [])
        self.assertNotEqual(before, after)


class VideoRulesTest(unittest.TestCase):
    def row(self, **values):
        return ListingVideo(**{"duration_seconds": 8, "size_bytes": 1000, "mime_type": "video/mp4", **values})

    def test_each_marketplace_refuses_what_its_own_rules_refuse(self):
        problem = listing_videos.video_problem
        self.assertIsNone(problem(self.row(), "poshmark"))
        self.assertIn("shorter than 15", problem(self.row(duration_seconds=15), "poshmark"))
        self.assertIsNone(problem(self.row(duration_seconds=59, mime_type="video/quicktime"), "ebay"))
        self.assertIn("150 MB", problem(self.row(size_bytes=157_286_401), "ebay"))
        self.assertIn("format", problem(self.row(mime_type="video/webm"), "ebay"))
        self.assertIsNone(problem(self.row(duration_seconds=3), "etsy"))
        self.assertIsNone(problem(self.row(duration_seconds=15), "etsy"))
        self.assertIn("3 to 15 seconds", problem(self.row(duration_seconds=2.9), "etsy"))
        self.assertIn("3 to 15 seconds", problem(self.row(duration_seconds=15.1), "etsy"))
        self.assertIn("95 MB", problem(self.row(size_bytes=95_000_001), "etsy"))
        for marketplace in ("depop", "mercari", "vinted", "sellwild"):
            self.assertIn("does not take listing videos", problem(self.row(), marketplace))

    def test_video_ids_are_never_category_fields_or_vendoo_fields(self):
        from vendoo_studio.services import ebay_listings, etsy_listings
        from vendoo_studio.services.vendoo_item_build import _listing_section

        listing = {"ebay_specifics": {"videoId": "abc", "Brand": "Levi's"}, "etsy_specifics": {"videoId": "abc"}}
        self.assertEqual(ebay_listings.ebay_aspects(listing, {}), {"Brand": ["Levi's"]})
        self.assertNotIn("videoid", etsy_listings._answers(listing))
        for marketplace in ("ebay", "etsy", "poshmark"):
            section = _listing_section(marketplace, {f"{marketplace}_specifics": {"videoId": "abc"}}, {})
            self.assertNotIn("videoId", str(section))
