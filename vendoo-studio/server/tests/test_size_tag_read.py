"""Reading the clothing size off an item's size tag in the bulk upload."""
from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from vendoo_studio.main import app
from vendoo_studio.routes import measurements as route
from vendoo_studio.services.seller_overrides import notes_for_listing_edits
from vendoo_studio.services.size_tag_read import settle


def _photo() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (400, 400), (40, 40, 45)).save(out, "JPEG")
    return out.getvalue()


class FakeProvider:
    """Gives each independent read the answer it is handed, in order."""

    name, vision_model = "openrouter", "google/gemini-flash-latest"

    def __init__(self, answers: list[dict]):
        self.answers = list(answers)
        self.calls: list[list[dict]] = []

    async def vision_chat(self, messages):
        self.calls.append(messages)
        yield "```json\n" + json.dumps(self.answers.pop(0)) + "\n```"


class SettleTest(unittest.TestCase):
    def test_two_reads_that_agree_are_accepted(self):
        result = settle({"tag": "found", "sizes": ["M"]}, {"tag": "found", "sizes": ["m"]})
        self.assertEqual((result["status"], result["size"]), ("accepted", "M"))

    def test_size_zero_is_a_size(self):
        for first, second in (("0", "0"), (0, "0"), (0, 0)):
            with self.subTest(first=first, second=second):
                result = settle({"tag": "found", "sizes": [first]}, {"tag": "found", "sizes": [second]})
                self.assertEqual((result["status"], result["size"]), ("accepted", "0"))

    def test_spellings_of_one_size_agree(self):
        result = settle({"tag": "found", "sizes": ["32 x 30"]}, {"tag": "found", "sizes": ["32×30"]})
        self.assertEqual((result["status"], result["size"]), ("accepted", "32 x 30"))

    def test_two_reads_that_disagree_are_not_guessed(self):
        result = settle({"tag": "found", "sizes": ["M"]}, {"tag": "found", "sizes": ["L"]})
        self.assertEqual(result, {"status": "conflict", "size": None, "sizes": ["M", "L"]})

    def test_two_tags_with_different_sizes_are_a_conflict(self):
        both = {"tag": "found", "sizes": ["S", "XL"]}
        self.assertEqual(settle(both, both)["status"], "conflict")

    def test_no_tag_in_either_read(self):
        self.assertEqual(settle({"tag": "none", "sizes": []}, {"tag": "none"})["status"], "not_found")

    def test_an_unreadable_or_one_sided_read_needs_the_seller(self):
        for second in ({"tag": "unreadable", "sizes": []}, {"tag": "none", "sizes": []}, {}, {"tag": "found", "sizes": [""]}):
            with self.subTest(second=second):
                result = settle({"tag": "found", "sizes": ["M"]}, second)
                self.assertEqual((result["status"], result["size"]), ("unreadable", None))


class ReadSizeTagsRouteTest(unittest.TestCase):
    def post(self, provider, files):
        with patch.object(route, "get_photo_provider", return_value=provider):
            return TestClient(app).post(
                "/api/size-tags/read",
                files=[("files", (f"{i}.jpg", body, "image/jpeg")) for i, body in enumerate(files)],
            )

    def test_reads_the_tag_twice_and_names_no_model(self):
        provider = FakeProvider([{"tag": "found", "sizes": ["XL"]}, {"tag": "found", "sizes": ["XL"]}])
        with self.assertLogs("vendoo_studio.services.size_tag_read", "INFO") as logs:
            response = self.post(provider, [_photo(), _photo()])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"photos_read": 2, "status": "accepted", "size": "XL", "sizes": ["XL"]})
        self.assertEqual(len(provider.calls), 2)
        # The seller-facing answer never names the provider; the server log keeps it.
        self.assertNotIn("gemini", response.text.casefold())
        self.assertIn("google/gemini-flash-latest", "\n".join(logs.output))
        # Each read sees every photo and is told what is not a size tag.
        system, user = provider.calls[0]
        self.assertIn("brand tab", system["content"])
        self.assertEqual(sum(1 for part in user["content"] if part["type"] == "image_url"), 2)

    def test_a_conflict_comes_back_without_a_size(self):
        provider = FakeProvider([{"tag": "found", "sizes": ["8"]}, {"tag": "found", "sizes": ["6"]}])
        body = self.post(provider, [_photo()]).json()
        self.assertEqual((body["status"], body["size"]), ("conflict", None))

    def test_needs_a_connected_model(self):
        response = self.post(None, [_photo()])
        self.assertEqual(response.status_code, 503)
        self.assertIn("size tags", response.json()["detail"])

    def test_a_stalled_model_is_named_in_the_error(self):
        class Stalled:
            async def vision_chat(self, messages):
                raise RuntimeError("OpenRouter stopped answering while looking at the photos.")
                yield ""  # pragma: no cover

        response = self.post(Stalled(), [_photo()])
        self.assertEqual(response.status_code, 502)
        self.assertIn("OpenRouter", response.json()["detail"])

    def test_limits_photos_per_listing(self):
        self.assertEqual(self.post(FakeProvider([]), [_photo()] * 21).status_code, 400)


class EditedSizeLeavesTheTagTest(unittest.TestCase):
    """Once Size changes in the editor the tag size no longer applies. The edit is a seller lock;
    it is not recorded as a tag size, since chat, templates and normalizers edit Size too."""

    def test_a_changed_size_clears_the_tag_size(self):
        for submitted in ({"size": "L"}, {"size": 0}, {}):
            with self.subTest(submitted=submitted):
                self.assertEqual(notes_for_listing_edits({"size": "M"}, submitted), {"size": ""})
        self.assertEqual(notes_for_listing_edits({"size": "M"}, {"size": "M"}), {})


if __name__ == "__main__":
    unittest.main()
