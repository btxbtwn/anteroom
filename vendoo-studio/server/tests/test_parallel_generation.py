"""Parallel generation agents: the agent event contract, Brave's monthly quota,
stage timings, and the per-marketplace specifics fill."""

from __future__ import annotations

import asyncio
import contextvars
import json
import unittest
from datetime import UTC, datetime
from unittest import mock

from vendoo_studio.services import activity, live_trace, model_slots


def _traced(coro_factory):
    events: list[tuple[str, str]] = []

    async def main():
        live_trace.bind(lambda kind, text: events.append((kind, text)))
        return await coro_factory()

    result = contextvars.Context().run(asyncio.run, main())
    return result, events


def _agent_states(events):
    return [json.loads(text) for kind, text in events if kind == "agent"]


class AgentContractTest(unittest.TestCase):
    def test_reports_start_progress_and_result_in_the_contract_shape(self):
        async def work():
            async with live_trace.agent("comps-sold", "Sold comps") as agent:
                live_trace.emit("step", "Claude searched: levis 501 sold")
                live_trace.emit("thinking", "pondering prices")
                agent.result("Found 8 sold comps")

        _, events = _traced(work)
        # Steps and thinking inside the agent never reach the main trace.
        self.assertEqual([kind for kind, _ in events], ["agent", "agent", "agent"])
        start, progress, end = _agent_states(events)
        self.assertEqual(
            set(start), {"id", "label", "status", "detail", "started_at", "ended_at"},
        )
        self.assertTrue(start["id"].startswith("comps-sold-"))
        self.assertEqual(start["label"], "Sold comps")
        self.assertEqual((start["status"], start["detail"], start["ended_at"]), ("running", None, None))
        self.assertIsInstance(start["started_at"], int)
        self.assertEqual(progress["detail"], "Claude searched: levis 501 sold")
        self.assertEqual(end["status"], "done")
        self.assertEqual(end["detail"], "Found 8 sold comps")
        self.assertGreaterEqual(end["ended_at"], end["started_at"])
        self.assertEqual({state["id"] for state in (start, progress, end)}, {start["id"]})

    def test_outside_an_agent_emit_is_unchanged(self):
        async def work():
            async with live_trace.agent("categories", "Categories"):
                pass
            live_trace.emit("step", "main line")
            live_trace.emit("thinking", "main thought")

        _, events = _traced(work)
        self.assertEqual(events[-2:], [("step", "main line"), ("thinking", "main thought")])

    def test_child_tasks_and_nested_agents_report_to_the_innermost(self):
        async def work():
            async with live_trace.agent("outer", "Outer"):
                async def child():
                    live_trace.emit("step", "from the outer child")

                await asyncio.create_task(child())
                async with live_trace.agent("inner", "Inner"):
                    live_trace.emit("step", "from the inner")
                live_trace.emit("step", "outer again")

        _, events = _traced(work)
        details = [(state["label"], state["detail"]) for state in _agent_states(events)]
        self.assertIn(("Outer", "from the outer child"), details)
        self.assertIn(("Inner", "from the inner"), details)
        self.assertIn(("Outer", "outer again"), details)

    def test_ids_are_unique(self):
        async def work():
            for _ in range(2):
                async with live_trace.agent("specifics-ebay", "eBay fields"):
                    pass

        _, events = _traced(work)
        self.assertEqual(len({state["id"] for state in _agent_states(events)}), 2)

    def test_failure_is_reported_and_re_raised(self):
        async def work():
            async with live_trace.agent("comps-brave", "Brave search"):
                raise RuntimeError("Brave HTTP 402")

        events: list = []

        async def main():
            live_trace.bind(lambda kind, text: events.append((kind, text)))
            try:
                await work()
            except RuntimeError:
                pass

        contextvars.Context().run(asyncio.run, main())
        end = _agent_states(events)[-1]
        self.assertEqual((end["status"], end["detail"]), ("failed", "Brave HTTP 402"))
        self.assertIsNotNone(end["ended_at"])

    def test_cancellation_is_reported_and_re_raised(self):
        events: list = []

        async def main():
            live_trace.bind(lambda kind, text: events.append((kind, text)))

            async def work():
                async with live_trace.agent("comps-active", "Active listings"):
                    await asyncio.Event().wait()

            task = asyncio.create_task(work())
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        contextvars.Context().run(asyncio.run, main())
        self.assertEqual(_agent_states(events)[-1]["status"], "cancelled")

    def test_without_a_sink_agents_still_run(self):
        async def work():
            async with live_trace.agent("x", "X") as agent:
                agent.update("progress")
                return 42

        self.assertEqual(contextvars.Context().run(asyncio.run, work()), 42)


class AgentActivityTest(unittest.TestCase):
    def tearDown(self):
        activity.clear_agents("conv-a")

    def test_keeps_the_latest_state_per_agent(self):
        run = activity.begin_agents("conv-a")
        activity.record_agent("conv-a", run, json.dumps({"id": "a-1", "status": "running", "started_at": 2}))
        activity.record_agent("conv-a", run, json.dumps({"id": "b-2", "status": "running", "started_at": 1}))
        activity.record_agent("conv-a", run, json.dumps({"id": "a-1", "status": "done", "started_at": 2}))
        activity.record_agent("conv-a", run, "not json")
        self.assertEqual(
            [(state["id"], state["status"]) for state in activity.agents("conv-a")],
            [("b-2", "running"), ("a-1", "done")],
        )
        activity.clear_agents("conv-a")
        self.assertEqual(activity.agents("conv-a"), [])

    def test_a_stale_run_cannot_write_into_the_new_one(self):
        old = activity.begin_agents("conv-a")
        new = activity.begin_agents("conv-a")
        # The old run's agents are cancelled while it unwinds, after the new one began.
        activity.record_agent("conv-a", old, json.dumps({"id": "old-1", "status": "cancelled"}))
        activity.record_agent("conv-a", new, json.dumps({"id": "new-1", "status": "running"}))
        self.assertEqual([state["id"] for state in activity.agents("conv-a")], ["new-1"])

    def test_deleting_the_listing_drops_its_agents(self):
        from vendoo_studio.database import SessionLocal
        from vendoo_studio.repositories.queries import ConversationRepo
        from vendoo_studio.services.listing_delete import delete_listing

        db = SessionLocal()
        try:
            conv = ConversationRepo(db).create(title="Delete me")
            run = activity.begin_agents(conv.id)
            activity.record_agent(conv.id, run, json.dumps({"id": "x-1", "status": "done"}))
            delete_listing(db, conv.id)
            self.assertEqual(activity.agents(conv.id), [])
        finally:
            db.close()


class ModelSlotsTest(unittest.TestCase):
    def test_caps_concurrent_calls(self):
        running = 0
        peak = 0

        async def call():
            nonlocal running, peak
            async with model_slots.slot():
                running += 1
                peak = max(peak, running)
                await asyncio.sleep(0.01)
                running -= 1

        async def main():
            model_slots.bind(2)
            await asyncio.gather(*(asyncio.create_task(call()) for _ in range(5)))

        contextvars.Context().run(asyncio.run, main())
        self.assertEqual(peak, 2)


class BraveQuotaMonthTest(unittest.TestCase):
    def setUp(self):
        from vendoo_studio.services.brave_search import reset_quota_backoff

        reset_quota_backoff()
        self.addCleanup(reset_quota_backoff)

    def test_spent_until_the_next_utc_month_and_survives_restarts(self):
        from vendoo_studio.services import brave_search
        from vendoo_studio.services.user_settings import read_settings

        october = datetime(2026, 10, 31, 23, 0, tzinfo=UTC)
        brave_search._note_quota_exhausted("Brave HTTP 402: USAGE_LIMIT_EXCEEDED", now=october)
        # Persisted in settings.json, not process memory.
        self.assertEqual(read_settings()[brave_search.QUOTA_SETTING]["month"], "2026-10")
        self.assertIn("402", brave_search.quota_exhausted(now=october))
        self.assertIsNone(brave_search.quota_exhausted(now=datetime(2026, 11, 1, 0, 0, tzinfo=UTC)))

    def test_reset_clears_it(self):
        from vendoo_studio.services import brave_search

        brave_search._note_quota_exhausted("Brave HTTP 402")
        brave_search.reset_quota_backoff()
        self.assertIsNone(brave_search.quota_exhausted())

    def test_usage_limit_on_any_status_marks_it_spent(self):
        from vendoo_studio.services import brave_search

        class FakeResp:
            status_code = 429
            text = '{"error": {"code": "USAGE_LIMIT_EXCEEDED"}}'

            def json(self):
                return {"error": {"code": "USAGE_LIMIT_EXCEEDED"}}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url, headers=None, params=None):
                return FakeResp()

        with mock.patch("vendoo_studio.services.brave_search.httpx.AsyncClient", FakeClient):
            with self.assertRaises(RuntimeError):
                asyncio.run(brave_search.search_web("q", "BSA-test"))
        self.assertIsNotNone(brave_search.quota_exhausted())


class GenerationTimingTest(unittest.TestCase):
    def setUp(self):
        from vendoo_studio.database import SessionLocal
        from vendoo_studio.models.generation_timing import GenerationTiming

        self.db = SessionLocal()
        self.db.query(GenerationTiming).delete()
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_records_stream_stages_then_appends_the_finish(self):
        from vendoo_studio.services import generation_timing

        timing_id = generation_timing.record_run(
            "conv-t", {"photo_analysis": 10.123, "listing_stream": 20}, total_sec=35.0, outcome="done",
        )
        generation_timing.add_stages(timing_id, {"gap_fill": 5, "vendoo_fields": 7}, total_sec=48.0)
        [run] = generation_timing.for_conversation(self.db, "conv-t")
        self.assertEqual(run["stages"], {
            "photo_analysis": 10.12, "listing_stream": 20.0, "gap_fill": 5.0, "vendoo_fields": 7.0,
        })
        self.assertEqual(run["total_sec"], 48.0)
        self.assertEqual(run["outcome"], "done")

    def test_summary_medians_finished_runs_only(self):
        from vendoo_studio.services import generation_timing

        for seconds in (10, 20, 30):
            generation_timing.record_run("conv-s", {"photo_analysis": seconds}, total_sec=seconds * 2, outcome="done")
        generation_timing.record_run("conv-s", {"photo_analysis": 500}, total_sec=500, outcome="failed")
        summary = generation_timing.recent_summary(self.db)
        self.assertEqual(len(summary["runs"]), 4)
        self.assertEqual(summary["finished"], 3)
        self.assertEqual(summary["median_stage_sec"], {"photo_analysis": 20.0})
        self.assertEqual(summary["median_total_sec"], 40.0)




def _specs(**fields):
    from vendoo_studio.services.vendoo_specifics import normalize_specifics

    raw = {}
    for key, (options, required, multi) in fields.items():
        raw[key] = {
            "id": key,
            "display": key,
            "options": {str(i): {"id": v, "display": v} for i, v in enumerate(options)},
            "rules": {"fieldOptions": {
                "minValues": 1 if required else 0,
                "maxValues": 2 if multi else 1,
                "selectionMode": "SelectionOnly" if options else "FreeText",
            }},
        }
    return normalize_specifics(raw)




if __name__ == "__main__":
    unittest.main()
