from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from vendoo_studio.main import app
from vendoo_studio.routes import settings as settings_routes
from vendoo_studio.services import usage_limits, user_settings

CHECKED_AT = "2026-10-21T05:00:00Z"

# What `/backend-api/wham/usage` returns for a paid ChatGPT plan.
CHATGPT_PLUS = {
    "plan_type": "plus",
    "rate_limit": {
        "primary_window": {"used_percent": 42.5, "reset_at": 1792560000, "limit_window_seconds": 18000},
        "secondary_window": {"used_percent": 130, "reset_at": 1792900000, "limit_window_seconds": 604800},
    },
}

# Claude Code's `get_usage` control response (trimmed from a real Max account).
CLAUDE_MAX = {
    "subscription_type": "max",
    "rate_limits_available": True,
    "rate_limits": {
        "five_hour": {"utilization": 28, "resets_at": "2026-10-21T08:40:00.369358+00:00"},
        "seven_day": {"utilization": 30, "resets_at": "2026-10-24T17:00:00.369378+00:00"},
        "seven_day_opus": None,
        "extra_usage": {"is_enabled": False, "monthly_limit": None, "used_credits": 0, "utilization": None},
        "limits": [
            {"kind": "session", "percent": 28, "resets_at": "2026-10-21T08:40:00+00:00", "scope": None},
            {
                "kind": "weekly_scoped",
                "percent": 4,
                "resets_at": "2026-10-24T17:00:00+00:00",
                "scope": {"model": {"id": None, "display_name": "Fable"}},
            },
        ],
        "spend": {"enabled": False, "used": {"amount_minor": 0, "currency": "USD", "exponent": 2}, "limit": None},
    },
}

# Cursor's GetCurrentPeriodUsage (Connect JSON: int64 fields arrive as strings).
CURSOR_PRO = {
    "billingCycleStart": "1792415259000",
    "billingCycleEnd": "1795093659000",
    "planUsage": {
        "totalSpend": 15991,
        "limit": 7000,
        "autoPercentUsed": 13.325833333333334,
        "apiPercentUsed": 0,
        "totalPercentUsed": 12.206870229007635,
    },
}


class ChatGptParserTest(unittest.TestCase):
    def test_paid_plan_has_session_and_weekly(self):
        limits = usage_limits.chatgpt_usage_to_limits(CHATGPT_PLUS, CHECKED_AT)
        self.assertEqual(limits["provider"], "chatgpt")
        self.assertEqual(limits["plan"], "Plus")
        self.assertNotIn("unavailable", limits)
        session, weekly = limits["windows"]
        self.assertEqual(
            session,
            {
                "id": "primary",
                "kind": "session",
                "label": "Session",
                "used_percent": 42.5,
                "window_duration_mins": 300,
                "resets_at": "2026-10-21T05:20:00Z",
            },
        )
        self.assertEqual(weekly["kind"], "weekly")
        self.assertEqual(weekly["used_percent"], 100.0)
        self.assertEqual(weekly["window_duration_mins"], 10080)

    def test_free_plan_without_duration_is_monthly(self):
        body = {"plan_type": "free", "rate_limit": {"primary_window": {"used_percent": 10, "reset_at": None}}}
        limits = usage_limits.chatgpt_usage_to_limits(body, CHECKED_AT)
        self.assertEqual(limits["plan"], "Free")
        [window] = limits["windows"]
        self.assertEqual((window["kind"], window["label"], window["window_duration_mins"]), ("monthly", "Monthly", 43200))
        self.assertNotIn("resets_at", window)

    def test_missing_windows(self):
        limits = usage_limits.chatgpt_usage_to_limits({"plan_type": "pro", "rate_limit": None}, CHECKED_AT)
        self.assertEqual(limits["windows"], [])
        limits = usage_limits.chatgpt_usage_to_limits({"rate_limit": {"primary_window": {"used_percent": "x"}}}, CHECKED_AT)
        self.assertEqual(limits["windows"], [])
        self.assertNotIn("plan", limits)


class ClaudeParserTest(unittest.TestCase):
    def test_windows_and_scoped_model(self):
        limits = usage_limits.claude_usage_to_limits(CLAUDE_MAX, CHECKED_AT)
        self.assertEqual(limits["plan"], "Max")
        self.assertEqual(
            [(w["id"], w["kind"], w["label"], w["used_percent"]) for w in limits["windows"]],
            [
                ("five_hour", "session", "Session", 28.0),
                ("seven_day", "weekly", "Weekly", 30.0),
                ("seven_day_fable", "weekly", "Weekly · Fable", 4.0),
            ],
        )
        self.assertEqual(limits["windows"][0]["resets_at"], "2026-10-21T08:40:00.369358Z")

    def test_model_scoped_field_wins_and_spend(self):
        response = {
            "rate_limits_available": True,
            "rate_limits": {
                "seven_day": {"utilization": 55, "resets_at": None},
                "model_scoped": [{"display_name": "Opus 5", "utilization": 70, "resets_at": None}],
                "spend": {
                    "enabled": True,
                    "used": {"amount_minor": 2500, "currency": "USD", "exponent": 2},
                    "limit": {"amount_minor": 10000, "currency": "USD", "exponent": 2},
                },
            },
        }
        limits = usage_limits.claude_usage_to_limits(response, CHECKED_AT)
        self.assertEqual(
            [(w["id"], w["used_percent"]) for w in limits["windows"]],
            [("seven_day", 55.0), ("seven_day_opus_5", 70.0), ("monthly_spend", 25.0)],
        )

    def test_extra_usage_spend(self):
        response = {
            "rate_limits_available": True,
            "rate_limits": {"extra_usage": {"is_enabled": True, "used_credits": 30, "monthly_limit": 120}},
        }
        [spend] = usage_limits.claude_usage_to_limits(response, CHECKED_AT)["windows"]
        self.assertEqual((spend["label"], spend["used_percent"]), ("Monthly spend", 25.0))

    def test_unavailable(self):
        limits = usage_limits.claude_usage_to_limits({"rate_limits_available": False}, CHECKED_AT)
        self.assertEqual(limits["unavailable"]["reason"], "unsupported")
        self.assertEqual(limits["windows"], [])
        limits = usage_limits.claude_usage_to_limits({"rate_limits_available": True, "rate_limits": None}, CHECKED_AT)
        self.assertEqual(limits["unavailable"], {"reason": "probeFailed"})


class CursorParserTest(unittest.TestCase):
    def test_plan_usage(self):
        limits = usage_limits.cursor_usage_to_limits(CURSOR_PRO, CHECKED_AT)
        self.assertEqual(
            [(w["id"], w["label"], round(w["used_percent"], 1)) for w in limits["windows"]],
            [
                ("totalPercentUsed", "Overall", 12.2),
                ("autoPercentUsed", "Cursor Models", 13.3),
                ("apiPercentUsed", "Other Models", 0.0),
            ],
        )
        self.assertTrue(all(w["kind"] == "monthly" for w in limits["windows"]))
        self.assertTrue(all(w["resets_at"] == "2026-11-19T13:07:39Z" for w in limits["windows"]))
        self.assertIn("Grok and Composer", limits["windows"][1]["description"])

    def test_numeric_cycle_end_and_empty_plan_usage(self):
        limits = usage_limits.cursor_usage_to_limits(
            {"billingCycleEnd": 1795093659000, "planUsage": {"totalPercentUsed": 50}}, CHECKED_AT
        )
        self.assertEqual(limits["windows"][0]["resets_at"], "2026-11-19T13:07:39Z")
        empty = usage_limits.cursor_usage_to_limits({"billingCycleEnd": "0", "planUsage": {}}, CHECKED_AT)
        self.assertEqual(empty["unavailable"]["reason"], "unsupported")
        self.assertEqual(usage_limits.cursor_usage_to_limits({}, CHECKED_AT)["unavailable"]["reason"], "unsupported")


class FetcherTest(unittest.TestCase):
    def test_signed_out_providers_are_unsupported(self):
        with (
            mock.patch("vendoo_studio.services.chatgpt_oauth.chatgpt_signed_in", return_value=False),
            mock.patch("vendoo_studio.services.claude_auth.claude_signed_in", return_value=False),
            mock.patch("vendoo_studio.services.keychain.get_cursor_api_key", return_value=None),
        ):
            chatgpt = asyncio.run(usage_limits.fetch_chatgpt())
            claude = asyncio.run(usage_limits.fetch_claude())
            cursor = asyncio.run(usage_limits.fetch_cursor())
        for limits in (chatgpt, claude, cursor):
            self.assertEqual(limits["unavailable"]["reason"], "unsupported")
            self.assertIn("Settings → Providers", limits["unavailable"]["message"])
        mimo = asyncio.run(usage_limits.fetch_mimo())
        self.assertEqual(mimo["unavailable"]["message"], "Xiaomi MiMo does not report usage limits.")

    def test_claude_reads_get_usage(self):
        async def fake_usage():
            return CLAUDE_MAX

        with (
            mock.patch("vendoo_studio.services.claude_auth.claude_signed_in", return_value=True),
            mock.patch.object(usage_limits, "_claude_get_usage", fake_usage),
        ):
            limits = asyncio.run(usage_limits.fetch_claude())
        self.assertEqual(len(limits["windows"]), 3)

    def test_claude_failure_is_probe_failed(self):
        async def broken():
            raise RuntimeError("secret-token-should-not-leak")

        with (
            mock.patch("vendoo_studio.services.claude_auth.claude_signed_in", return_value=True),
            mock.patch.object(usage_limits, "_claude_get_usage", broken),
        ):
            limits = asyncio.run(usage_limits.fetch_claude())
        self.assertEqual(limits["unavailable"]["reason"], "probeFailed")
        self.assertNotIn("secret", str(limits))


class CacheTest(unittest.TestCase):
    def setUp(self):
        usage_limits.clear_cache()
        self.calls = 0

    def tearDown(self):
        usage_limits.clear_cache()

    def _fetcher(self, result):
        async def fetch():
            self.calls += 1
            return result

        return fetch

    def test_success_is_cached_until_forced(self):
        ok = usage_limits.make_limits("mimo", CHECKED_AT, [])
        with mock.patch.dict(usage_limits.FETCHERS, {"mimo": self._fetcher(ok)}):
            asyncio.run(usage_limits.provider_usage_limits("mimo"))
            asyncio.run(usage_limits.provider_usage_limits("mimo"))
            self.assertEqual(self.calls, 1)
            asyncio.run(usage_limits.provider_usage_limits("mimo", force=True))
            self.assertEqual(self.calls, 2)

    def test_failures_expire_sooner(self):
        failed = usage_limits.make_unavailable("mimo", CHECKED_AT, "probeFailed")
        clock = [1000.0]
        with (
            mock.patch.dict(usage_limits.FETCHERS, {"mimo": self._fetcher(failed)}),
            mock.patch.object(usage_limits.time, "monotonic", lambda: clock[0]),
        ):
            asyncio.run(usage_limits.provider_usage_limits("mimo"))
            clock[0] += usage_limits.FAILURE_TTL_S - 1
            asyncio.run(usage_limits.provider_usage_limits("mimo"))
            self.assertEqual(self.calls, 1)
            clock[0] += 2
            asyncio.run(usage_limits.provider_usage_limits("mimo"))
            self.assertEqual(self.calls, 2)

    def test_crashing_fetcher_reports_probe_failed(self):
        async def crash():
            raise ValueError("boom")

        with mock.patch.dict(usage_limits.FETCHERS, {"mimo": crash}):
            limits = asyncio.run(usage_limits.provider_usage_limits("mimo"))
        self.assertEqual(limits["unavailable"]["reason"], "probeFailed")


class UsageRouteTest(unittest.TestCase):
    def setUp(self):
        usage_limits.clear_cache()
        self.client = TestClient(app)

    def tearDown(self):
        usage_limits.clear_cache()

    def test_route_lists_every_provider_in_order(self):
        def fake(provider):
            async def fetch():
                return usage_limits.make_limits(provider, CHECKED_AT, [])

            return fetch

        fakes = {provider: fake(provider) for provider in usage_limits.PROVIDER_ORDER}
        with mock.patch.dict(usage_limits.FETCHERS, fakes):
            response = self.client.get("/api/settings/usage-limits?refresh=1")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual([p["provider"] for p in body["providers"]], ["chatgpt", "claude", "cursor", "mimo"])


class CursorModelLabelsTest(unittest.TestCase):
    def test_models_route_names_each_catalog_model(self):
        catalog = [
            SimpleNamespace(id="grok-4.7", display_name="Grok 4.7"),
            SimpleNamespace(id="claude-opus-5-5", display_name=" Claude Opus 5.5 "),
            SimpleNamespace(id="composer-2.5", display_name=""),
        ]

        async def fake_catalog(key):
            return catalog

        with (
            mock.patch("vendoo_studio.services.keychain.get_cursor_api_key", return_value="crsr_test"),
            mock.patch.object(settings_routes, "_cursor_catalog", fake_catalog),
            mock.patch.object(settings_routes, "_cursor_reasoning_payload", return_value=None),
        ):
            response = TestClient(app).get("/api/settings/cursor/models")
        self.assertEqual(response.status_code, 200)
        labels = response.json()["labels"]
        self.assertEqual(labels["auto"], "Auto")
        self.assertEqual(labels["grok-4.7"], "Grok 4.7")
        self.assertEqual(labels["claude-opus-5-5"], "Claude Opus 5.5")
        self.assertNotIn("composer-2.5", labels)
        self.assertIn("grok-4.7", response.json()["models"])

    def test_labels_without_key_still_name_auto(self):
        with mock.patch("vendoo_studio.services.keychain.get_cursor_api_key", return_value=None):
            response = TestClient(app).get("/api/settings/cursor/models")
        self.assertEqual(response.json()["labels"], {"auto": "Auto"})


class ModelFavoritesTest(unittest.TestCase):
    def setUp(self):
        self._data = os.environ.get("VENDOO_STUDIO_DATA_DIR")
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["VENDOO_STUDIO_DATA_DIR"] = self.tmp.name
        self.client = TestClient(app)

    def tearDown(self):
        self.tmp.cleanup()
        if self._data is None:
            os.environ.pop("VENDOO_STUDIO_DATA_DIR", None)
        else:
            os.environ["VENDOO_STUDIO_DATA_DIR"] = self._data

    def test_favorites_round_trip_and_dedupe(self):
        self.assertEqual(self.client.get("/api/settings/ui").json()["model_favorites"], [])
        saved = self.client.put(
            "/api/settings/ui",
            json={
                "model_favorites": [
                    {"provider": "cursor", "model": " grok-4.7 "},
                    {"provider": "claude", "model": "claude-opus-5-5"},
                    {"provider": "cursor", "model": "grok-4.7"},
                ]
            },
        )
        self.assertEqual(saved.status_code, 200)
        expected = [
            {"provider": "cursor", "model": "grok-4.7"},
            {"provider": "claude", "model": "claude-opus-5-5"},
        ]
        self.assertEqual(saved.json()["model_favorites"], expected)
        self.assertEqual(self.client.get("/api/settings/ui").json()["model_favorites"], expected)
        # Other prefs leave favorites alone.
        self.client.put("/api/settings/ui", json={"theme": "light"})
        self.assertEqual(user_settings.get_ui_prefs()["model_favorites"], expected)

    def test_invalid_favorites_are_rejected(self):
        for bad in (
            [{"provider": "gemini", "model": "x"}],
            [{"provider": "claude", "model": "  "}],
            [{"provider": "claude", "model": "m" * 201}],
            [{"provider": "claude"}],
        ):
            response = self.client.put("/api/settings/ui", json={"model_favorites": bad})
            self.assertEqual(response.status_code, 400, bad)

    def test_favorites_are_capped(self):
        many = [{"provider": "cursor", "model": f"model-{index}"} for index in range(120)]
        saved = self.client.put("/api/settings/ui", json={"model_favorites": many})
        self.assertEqual(len(saved.json()["model_favorites"]), user_settings.MAX_MODEL_FAVORITES)
