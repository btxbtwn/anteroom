from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from vendoo_studio.database import SessionLocal
from vendoo_studio.main import app
from vendoo_studio.repositories.queries import ConversationRepo, JobRepo, ListingRepo
from vendoo_studio.services import live_trace
from vendoo_studio.services.comp_research import CompsEvent
from vendoo_studio.services.price_drop import PRICE_DROP_SOURCE
from vendoo_studio.services.vendoo_watch import SYNCED_REVISION


class PriceDropRouteTest(unittest.TestCase):
    def setUp(self):
        # Sold items are read from the whole database, which parallel tests
        # share. Matching sales written by another test would turn the default
        # percent suggestion into a sell-through price, so start from none.
        outcomes = patch("vendoo_studio.services.sell_through.collect_outcomes", return_value=[])
        outcomes.start()
        self.addCleanup(outcomes.stop)
        self.db = SessionLocal()
        self.client = TestClient(app)
        self.conv = ConversationRepo(self.db).create(title="Price drop")
        ListingRepo(self.db).save_revision(
            self.conv.id,
            {
                "title": "Nike Tee",
                "description": "Soft tee.",
                "price": 48,
                "brand": "Nike",
                "category_path": "Tops > T-Shirts",
                "condition": "Pre-Owned - Good",
                "quantity": 1,
            },
            source="generation",
        )

    def tearDown(self):
        self.db.close()

    def test_preview_and_apply(self):
        with patch("vendoo_studio.services.price_drop.comps_search_available", return_value=False):
            preview = self.client.post(f"/api/conversations/{self.conv.id}/price-drop/preview")
        self.assertEqual(preview.status_code, 200, preview.text)
        body = preview.json()
        self.assertEqual(body["current_price"], 48)
        self.assertEqual(body["suggested_price"], 43)

        apply = self.client.post(
            f"/api/conversations/{self.conv.id}/price-drop",
            json={"price": 41, "percent": 15, "mode": "percent"},
        )
        self.assertEqual(apply.status_code, 200, apply.text)
        payload = apply.json()
        self.assertEqual(payload["price"], 41)
        self.assertEqual(payload["previous_price"], 48)

        revisions = ListingRepo(self.db).get_revisions(self.conv.id)
        self.assertEqual(revisions[0].source, PRICE_DROP_SOURCE)
        self.assertEqual(revisions[0].listing_json["price"], 41)

        listing = self.client.get(f"/api/conversations/{self.conv.id}/listing")
        self.assertEqual(listing.json()["listing"]["price"], 41)

    def test_price_changes_keep_a_direct_actions_sent_revision_and_video(self):
        revision = ListingRepo(self.db).get_revisions(self.conv.id)[0]
        snapshot = {**revision.listing_json, "_native_video": {"id": "clip", "checksum": "original"}}
        job = JobRepo(self.db).create(self.conv.id, revision.id, snapshot, status="failed", current_step="native_poshmark_save_draft")
        response = self.client.post(f"/api/conversations/{self.conv.id}/price-drop", json={"price": 41, "mode": "percent", "percent": 15})
        self.assertEqual(response.status_code, 200, response.text)
        self.db.refresh(job)
        self.assertEqual(job.listing_snapshot, snapshot)
        self.assertEqual(ListingRepo(self.db).get_revisions(self.conv.id)[0].listing_json["price"], 41)

    def test_comps_stream_reports_sources_then_the_preview(self):
        comps = (
            "Sold comps:\n"
            "Query: Nike T-Shirts sold comps\n"
            "Source: ChatGPT + Cursor\n"
            "\n"
            "- $20 · eBay · Nike Tee\n"
            "  https://www.ebay.com/itm/1\n"
            "- $18 · Poshmark · Nike Tee\n"
            "  https://poshmark.com/listing/2\n"
            "- $22 · Mercari · Nike Tee\n"
            "  https://www.mercari.com/item/3\n"
        )

        async def fake_stream(_analysis, evidence):
            self.assertEqual(evidence["brand"], "Nike")
            live_trace.emit("step", "Cursor searched: nike tee sold")
            yield CompsEvent("source", source="Cursor", state="searching")
            yield CompsEvent("source", source="Cursor", state="done", sold=3, live=0)
            yield CompsEvent("done", text=comps)

        with (
            patch("vendoo_studio.services.price_drop.comps_search_available", return_value=True),
            patch("vendoo_studio.services.comp_research.stream_sold_comps", new=fake_stream),
        ):
            res = self.client.post(f"/api/conversations/{self.conv.id}/price-drop/comps")
        self.assertEqual(res.status_code, 200, res.text)
        events = [
            (block.split("\n", 1)[0].removeprefix("event: "), block.split("data: ", 1)[1])
            for block in res.text.strip().split("\n\n")
            if block.startswith("event:")
        ]
        self.assertEqual(events[0], ("step", "Cursor searched: nike tee sold"))
        self.assertEqual(json.loads(events[1][1])["state"], "searching")
        self.assertEqual(json.loads(events[2][1])["sold"], 3)
        preview = json.loads(events[-1][1])
        self.assertEqual(events[-1][0], "preview")
        self.assertEqual(preview["comps"]["target_price"], 27)
        self.assertEqual(preview["suggested_mode"], "comps")
        self.assertTrue(res.text.rstrip().endswith("data: [DONE]"))

    def test_comps_stream_shows_agent_progress_and_results_as_steps(self):
        async def fake_stream(_analysis, evidence):
            async with live_trace.agent("comps-sold", "Sold comps") as agent:
                live_trace.emit("step", "Claude searched: nike tee sold")
                agent.result("Found 8 sold comps")
            try:
                async with live_trace.agent("comps-brave", "Brave search"):
                    raise RuntimeError("Brave HTTP 402")
            except RuntimeError:
                pass
            yield CompsEvent("done", text="Sold comps:\nQuery: Nike tee sold comps\n")

        with (
            patch("vendoo_studio.services.price_drop.comps_search_available", return_value=True),
            patch("vendoo_studio.services.comp_research.stream_sold_comps", new=fake_stream),
        ):
            res = self.client.post(f"/api/conversations/{self.conv.id}/price-drop/comps")
        self.assertEqual(res.status_code, 200, res.text)
        steps = [
            block.split("data: ", 1)[1]
            for block in res.text.strip().split("\n\n")
            if block.startswith("event: step")
        ]
        # Progress, the done result and the failure reason, each once.
        self.assertEqual(steps, ["Claude searched: nike tee sold", "Found 8 sold comps", "Brave HTTP 402"])

    def test_rejects_raise_or_missing_price(self):
        bad = self.client.post(
            f"/api/conversations/{self.conv.id}/price-drop",
            json={"price": 60, "mode": "custom"},
        )
        self.assertEqual(bad.status_code, 400)

        empty = ConversationRepo(self.db).create(title="Empty")
        missing = self.client.post(f"/api/conversations/{empty.id}/price-drop/preview")
        self.assertEqual(missing.status_code, 400)


class BatchMarkdownRouteTest(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self.client = TestClient(app)
        self.convs = ConversationRepo(self.db)
        self.listings = ListingRepo(self.db)
        self.tee = self._listing("Stale tee", 48)
        self.coat = self._listing("Stale coat", 120)

    def tearDown(self):
        self.db.close()

    def _listing(self, title: str, price, *, synced: bool = True):
        conv = self.convs.create(title=title)
        listing = {"title": title, "description": "Soft.", "condition": "Pre-Owned - Good", "quantity": 1}
        if price is not None:
            listing["price"] = price
        revision = self.listings.save_revision(conv.id, listing, source="generation")
        if synced:
            conv.notes = json.dumps({SYNCED_REVISION: revision.id})
            self.db.commit()
        return conv

    def _preview(self, *ids):
        with patch("vendoo_studio.services.price_drop.comps_search_available", return_value=False):
            res = self.client.post("/api/price-drop/batch/preview", json={"conversation_ids": list(ids)})
        self.assertEqual(res.status_code, 200, res.text)
        return {row["conversation_id"]: row for row in res.json()["rows"]}

    def test_preview_suggests_each_listing_and_reports_the_ones_it_cannot_drop(self):
        empty = self._listing("No price", None)
        busy = self._listing("Sending", 30)
        revision = self.listings.get_revisions(busy.id)[0]
        JobRepo(self.db).create(busy.id, revision.id, revision.listing_json, status="dispatched")

        with patch("vendoo_studio.services.sell_through.collect_outcomes", return_value=[]) as outcomes:
            rows = self._preview(self.tee.id, self.coat.id, empty.id, busy.id, "missing")
        outcomes.assert_called_once()

        tee = rows[self.tee.id]
        self.assertEqual(tee["title"], "Stale tee")
        self.assertEqual(tee["current_price"], 48)
        self.assertEqual(tee["suggested_price"], 43)
        self.assertTrue(tee["suggested_reason"])
        self.assertIsNone(tee["skip_reason"])
        self.assertEqual(tee["revision_id"], self.listings.get_revisions(self.tee.id)[0].id)
        self.assertEqual(rows[self.coat.id]["suggested_price"], 108)
        self.assertEqual(rows[empty.id]["skip_reason"], "Listing has no price to drop")
        self.assertEqual(rows[busy.id]["skip_reason"], "Anteroom is generating or sending this listing")
        self.assertEqual(rows["missing"]["skip_reason"], "It is no longer in Anteroom")
        self.assertEqual(self.listings.get_revisions(self.tee.id)[0].source, "generation")

    def test_apply_saves_price_drop_revisions_and_never_queues_a_send(self):
        rows = self._preview(self.tee.id, self.coat.id)
        res = self.client.post("/api/price-drop/batch", json={"drops": [
            {"conversation_id": self.tee.id, "price": 40, "expected_revision_id": rows[self.tee.id]["revision_id"]},
            {"conversation_id": self.coat.id, "price": 99.6},
        ]})
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["skipped"], [])
        applied = {row["conversation_id"]: row for row in body["applied"]}
        self.assertEqual(applied[self.tee.id]["price"], 40)
        self.assertEqual(applied[self.tee.id]["previous_price"], 48)
        self.assertEqual(applied[self.coat.id]["price"], 100)

        self.db.expire_all()
        for conv, price in ((self.tee, 40), (self.coat, 100)):
            latest = self.listings.get_revisions(conv.id)[0]
            self.assertEqual(latest.source, PRICE_DROP_SOURCE)
            self.assertEqual(latest.listing_json["price"], price)
            self.assertEqual(JobRepo(self.db).list_by_conversation(conv.id), [])

        listed = {row["id"]: row for row in self.client.get("/api/conversations").json()}
        self.assertTrue(listed[self.tee.id]["unsent_edits"])
        self.assertTrue(listed[self.coat.id]["unsent_edits"])

    def test_apply_skips_what_cannot_drop_and_saves_the_rest(self):
        rows = self._preview(self.tee.id, self.coat.id)
        stale_revision = rows[self.coat.id]["revision_id"]
        self.client.post(f"/api/conversations/{self.coat.id}/price-drop", json={"price": 110})
        empty = self._listing("No price", None)

        res = self.client.post("/api/price-drop/batch", json={"drops": [
            {"conversation_id": self.tee.id, "price": 48},
            {"conversation_id": self.coat.id, "price": 90, "expected_revision_id": stale_revision},
            {"conversation_id": empty.id, "price": 5},
            {"conversation_id": "missing", "price": 5},
        ]})
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["applied"], [])
        self.assertEqual({row["conversation_id"]: row["reason"] for row in body["skipped"]}, {
            self.tee.id: "New price must be lower than the current price",
            self.coat.id: "This listing changed since you reviewed it",
            empty.id: "Listing has no price to drop",
            "missing": "Conversation not found",
        })
        self.db.expire_all()
        self.assertEqual(self.listings.get_revisions(self.coat.id)[0].listing_json["price"], 110)

    def test_apply_skips_a_listing_studio_is_sending(self):
        revision = self.listings.get_revisions(self.tee.id)[0]
        JobRepo(self.db).create(self.tee.id, revision.id, revision.listing_json, status="queued")
        res = self.client.post("/api/price-drop/batch", json={"drops": [
            {"conversation_id": self.tee.id, "price": 40},
            {"conversation_id": self.coat.id, "price": 100},
        ]})
        body = res.json()
        self.assertEqual([row["conversation_id"] for row in body["applied"]], [self.coat.id])
        self.assertEqual(body["skipped"], [
            {"conversation_id": self.tee.id, "reason": "Anteroom is generating or sending this listing"},
        ])

    def test_empty_batches_are_rejected(self):
        self.assertEqual(self.client.post("/api/price-drop/batch/preview", json={"conversation_ids": []}).status_code, 422)
        self.assertEqual(self.client.post("/api/price-drop/batch", json={"drops": []}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
