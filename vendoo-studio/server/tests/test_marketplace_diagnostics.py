"""Failure reports retain bounded useful facts, not marketplace payloads."""
import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from vendoo_studio.services import browser_bridge
from vendoo_studio.services.error_alerts import build_report, render_email


def test_failed_offer_retains_context_in_email_without_changing_result(monkeypatch, caplog):
    result = {"ok": False, "error": "Preview rejected", "safe_to_retry": True}
    request = AsyncMock(return_value=result)
    monkeypatch.setattr(browser_bridge, "_request", request)
    payload = {"marketplace": "depop", "action": "offer", "discount_percent": 20,
               "listing_id": "private-listing", "token": "private-token"}
    with caplog.at_level(logging.ERROR):
        assert asyncio.run(browser_bridge.request(SimpleNamespace(id="private-job"),
                         "marketplace.promotion_check", payload, timeout=90)) is result
    report = build_report(caplog.records[-1])
    context = report["marketplace_context"]
    assert context["action"] == "offer"
    assert context["discount_percent"] == 20
    assert context["safe_to_retry"] is True
    assert context["timeout_ms"] == 90000
    assert context["elapsed_ms"] >= 0
    _, body = render_email(report, 0, None)
    assert "discount_percent" in body
    assert not any(s in body for s in ("private-listing", "private-token", "private-job"))
    request.assert_awaited_once()


def test_poshmark_failure_keeps_selected_inputs_not_listing_content(monkeypatch, caplog):
    monkeypatch.setattr(browser_bridge, "_request", AsyncMock(return_value={"ok": False, "error": "Minimum too low"}))
    payload = {"marketplace": "poshmark", "action": "list", "listing": {
        "price": "20", "title": "private-title", "photos": ["private-photo"], "cost": 3,
        "poshmark_specifics": {"poshmarkPrice": "18.50", "smartSell": {"enabled": True, "minimumPrice": "8"},
                               "sellerNotes": "private-notes"}}}
    with caplog.at_level(logging.ERROR):
        asyncio.run(browser_bridge.request(SimpleNamespace(id="job"), "marketplace.execute", payload))
    report = build_report(caplog.records[-1])
    assert report["marketplace_context"]["poshmark_price_input"] == 18.5
    assert report["marketplace_context"]["smart_sell_minimum_input"] == 8
    assert report["marketplace_context"]["smart_sell_enabled_input"] is True
    assert not any(s in str(report) for s in ("private-title", "private-photo", "private-notes", '"cost"'))
    assert "safe_to_retry" not in report["marketplace_context"]


def test_exception_and_hosted_read_keep_context_without_retry(monkeypatch, caplog):
    from vendoo_studio.services.hosted_browser_relay import BrowserRelay
    relay = BrowserRelay()
    request = AsyncMock(side_effect=RuntimeError("Chrome did not answer in time"))
    monkeypatch.setattr(relay, "_read", request)
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError):
        asyncio.run(relay.read("marketplace.promotion_check", {"marketplace": "mercari", "action": "offer"}, 90))
    context = build_report(caplog.records[-1])["marketplace_context"]
    assert context["timeout_ms"] == 90000
    assert context["action"] == "offer"
    assert "safe_to_retry" not in context
    request.assert_awaited_once()


def test_context_is_allowlisted_and_does_not_fragment_fingerprints():
    from vendoo_studio.services.marketplace_diagnostics import sanitize_context
    raw = {"action": "Bearer secret", "safe_to_retry": "yes", "floor_cents": True,
           "discount_percent": float("nan"), "price_cents": "token=secret", "elapsed_ms": float("inf"),
           "listing": {"token": "secret"}, "unknown": "private", "timeout_ms": -1}
    assert sanitize_context(raw) == {}
    def report(price):
        record = logging.LogRecord("vendoo_studio.marketplaces", logging.ERROR, __file__, 100, "Poshmark rejected", (), None)
        record.marketplace_context = {"action": "list", "poshmark_price_input": price, "token": "secret"}
        return build_report(record)
    first, second = report(10), report(25)
    assert first["fingerprint"] == second["fingerprint"]
    assert "secret" not in str(first)


def test_success_does_not_emit_diagnostic_email(monkeypatch, caplog):
    monkeypatch.setattr(browser_bridge, "_request", AsyncMock(return_value={"ok": True}))
    with caplog.at_level(logging.ERROR):
        asyncio.run(browser_bridge.request(SimpleNamespace(id="job"), "marketplace.execute", {"marketplace": "poshmark"}))
    assert not caplog.records


def test_nested_result_does_not_inherit_retry_safety(caplog):
    from vendoo_studio.services.marketplace_errors import report_result
    with caplog.at_level(logging.ERROR):
        report_result("depop", "result", {"safe_to_retry": True, "result": {"state": "unknown"}},
                      context={"safe_to_retry": True, "action": "offer"})
    assert "safe_to_retry" not in build_report(caplog.records[-1])["marketplace_context"]


def test_invalid_large_numbers_and_strings_cannot_break_reporting():
    from vendoo_studio.services.marketplace_diagnostics import sanitize_context
    assert sanitize_context({"price_cents": 10 ** 1000, "floor_cents": "1" * 10000}) == {}
