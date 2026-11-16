"""Every marketplace operation reports failures without replaying a request."""
import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from vendoo_studio.services.marketplace_errors import marketplace_call, report_result


@pytest.mark.parametrize("marketplace", ["ebay", "etsy", "poshmark", "mercari", "depop", "sellwild"])
@pytest.mark.parametrize("operation", ["list", "update", "delist", "save_draft", "import", "sales", "inspect", "offer"])
def test_all_marketplace_operations_report_scrubbed_failures(caplog, marketplace, operation):
    result = {"ok": False, "error": "Request rejected token=private-value", "error_code": "rejected",
              "marketplace_message": "HTTP 400 code 1003 Bearer private-token", "photos": ["private-photo"]}
    with caplog.at_level(logging.ERROR, logger="vendoo_studio.marketplaces"):
        report_result(marketplace, operation, result)
    assert marketplace in caplog.text and operation in caplog.text
    assert "1003" in caplog.text and "rejected" in caplog.text
    assert not any(value in caplog.text for value in ("private-value", "private-token", "private-photo"))


def test_nested_and_partial_failures_are_reported_but_success_and_pending_are_quiet(caplog):
    with caplog.at_level(logging.ERROR, logger="vendoo_studio.marketplaces"):
        report_result("ebay", "update", {"responses": [{"statusCode": 400, "errors": [{"message": "Invalid inventory"}]}]})
        report_result("depop", "result", {"finished": True, "result": {"state": "unknown"}})
        report_result("mercari", "sales", {"ok": True, "orders": [{"error": "private order data"}]})
        report_result("depop", "result", {"finished": False, "known": False})
    assert len(caplog.records) == 2
    assert "Invalid inventory" in caplog.text
    assert "private order data" not in caplog.text


def test_caught_exception_is_reported_and_never_retried(caplog):
    calls = []

    @marketplace_call("etsy")
    async def read():
        calls.append(True)
        raise RuntimeError("Network failed token=private-value")

    with caplog.at_level(logging.ERROR, logger="vendoo_studio.marketplaces"), pytest.raises(RuntimeError):
        asyncio.run(read())
    assert calls == [True]
    assert "Network failed" in caplog.text


def test_cancellation_is_not_reported_as_a_marketplace_failure(caplog):
    @marketplace_call("ebay")
    async def cancelled():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cancelled())
    assert not caplog.records


def test_chrome_operation_reports_error_reply_and_preserves_result(monkeypatch, caplog):
    from vendoo_studio.services import browser_bridge

    result = {"ok": False, "error": "Mercari refused update", "error_code": "rejected"}
    call = AsyncMock(return_value=result)
    monkeypatch.setattr(browser_bridge, "_request", call)
    with caplog.at_level(logging.ERROR, logger="vendoo_studio.marketplaces"):
        assert asyncio.run(browser_bridge.request(SimpleNamespace(id="job"), "marketplace.execute", {"marketplace": "mercari"})) is result
    assert "mercari marketplace.execute" in caplog.text
    call.assert_awaited_once()


def test_hosted_read_reports_failure_even_when_caller_handles_it(monkeypatch, caplog):
    from vendoo_studio.services.hosted_browser_relay import BrowserRelay

    relay = BrowserRelay()
    call = AsyncMock(return_value={"ok": False, "error": "Poshmark sales read rejected"})
    monkeypatch.setattr(relay, "_read", call)
    with caplog.at_level(logging.ERROR, logger="vendoo_studio.marketplaces"):
        result = asyncio.run(relay.read("marketplace.orders", {"marketplace": "poshmark"}, 1))
    assert result["ok"] is False
    assert "poshmark marketplace.orders" in caplog.text
    call.assert_awaited_once()


def test_ebay_caught_api_failure_reaches_alert_report(caplog):
    from vendoo_studio.services.ebay_client import EbayClient, EbayFailure
    from vendoo_studio.services.error_alerts import build_report

    with caplog.at_level(logging.ERROR, logger="vendoo_studio.marketplaces"), pytest.raises(EbayFailure):
        asyncio.run(EbayClient("private-token", ("seller", "connected")).request("GET", "invalid"))
    report = build_report(caplog.records[-1])
    assert "ebay EbayClient.request" in report["message"]
    assert "private-token" not in str(report)
    assert report["exception"]["type"].endswith("EbayFailure")


def test_etsy_caught_api_failure_reaches_alert_report(monkeypatch, caplog):
    from vendoo_studio.services import marketplace_accounts
    from vendoo_studio.services.error_alerts import build_report

    token = AsyncMock(side_effect=RuntimeError("Etsy network error token=private-token"))
    monkeypatch.setattr(marketplace_accounts, "access_token", token)
    with caplog.at_level(logging.ERROR, logger="vendoo_studio.marketplaces"), pytest.raises(RuntimeError):
        asyncio.run(marketplace_accounts.etsy_api("GET", "shops/1/receipts"))
    report = build_report(caplog.records[-1])
    assert "etsy etsy_api" in report["message"]
    assert "private-token" not in str(report)
    token.assert_awaited_once()


def _cooldown_reply(**overrides):
    return {'ok': False, 'error': 'Mercari refused the request.', 'error_code': 'rejected', 'safe_to_retry': True,
            'marketplace_message': 'You can make an offer again in 46 hours. (once every 3 days)', **overrides}


@pytest.mark.parametrize('marketplace,operation,action', [
    ('mercari', 'marketplace.promote', 'offer'), ('mercari', 'marketplace.promotion_result', 'offer'),
    ('mercari', 'marketplace.execute', 'offer'), ('mercari', 'marketplace.orders', 'offer'),
    ('mercari', 'marketplace.promotion_check', 'share'), ('mercari', 'marketplace.promotion_check', 'publish'),
    ('mercari', 'marketplace.promotion_check', 'delist'), ('mercari', 'marketplace.promotion_check', None),
    *[(market, 'marketplace.promotion_check', 'offer') for market in ('poshmark', 'depop', 'ebay', 'etsy', 'sellwild')],
])
def test_cooldown_words_in_other_contexts_remain_transport_errors(monkeypatch, caplog, marketplace, operation, action):
    from vendoo_studio.services import browser_bridge
    result = _cooldown_reply()
    request = AsyncMock(return_value=result)
    monkeypatch.setattr(browser_bridge, '_request', request)
    with caplog.at_level(logging.INFO, logger='vendoo_studio.marketplaces'):
        assert asyncio.run(browser_bridge.request(SimpleNamespace(id='fixture'), operation,
                         {'marketplace': marketplace, 'action': action})) is result
    assert [record.levelname for record in caplog.records] == ['ERROR']
    request.assert_awaited_once()


@pytest.mark.parametrize('result,levels', [
    ({'safe_to_retry': True, 'result': _cooldown_reply()}, ['ERROR']),
    ({'safe_to_retry': True, 'result': _cooldown_reply(safe_to_retry=None)}, ['ERROR']),
    ({'ok': True, 'safe_to_retry': True, 'responses': [_cooldown_reply(),
      {'ok': False, 'error_code': 'signed_out', 'error': 'Sign in again'}]}, ['ERROR', 'ERROR']),
    (_cooldown_reply(result={'state': 'unknown', 'error': 'Unconfirmed send'}), ['INFO', 'ERROR']),
    (_cooldown_reply(results=[{'ok': False, 'error': 'Other listing refused', 'safe_to_retry': False},
                             {'state': 'unknown'}]), ['INFO', 'ERROR', 'ERROR']),
])
def test_cooldown_transport_classification_does_not_leak_into_nested_envelopes(monkeypatch, caplog, result, levels):
    from copy import deepcopy
    from vendoo_studio.services import browser_bridge
    before = deepcopy(result)
    request = AsyncMock(return_value=result)
    monkeypatch.setattr(browser_bridge, '_request', request)
    with caplog.at_level(logging.INFO, logger='vendoo_studio.marketplaces'):
        assert asyncio.run(browser_bridge.request(SimpleNamespace(id='fixture'), 'marketplace.promotion_check',
                         {'marketplace': 'mercari', 'action': 'offer'})) is result
    assert [record.levelname for record in caplog.records] == levels
    assert result == before
    request.assert_awaited_once()
    if result.get('result', {}).get('state') == 'unknown':
        assert 'safe_to_retry' not in caplog.records[-1].marketplace_context


def test_cooldown_transport_classifier_does_not_trust_spoofed_outer_context(caplog):
    with caplog.at_level(logging.INFO, logger='vendoo_studio.marketplaces'):
        report_result('mercari', 'marketplace.promote', _cooldown_reply(), context={
            'operation': 'marketplace.promotion_check', 'action': 'offer', 'safe_to_retry': True})
        report_result('mercari', 'marketplace.promotion_check', _cooldown_reply(safe_to_retry=None), context={
            'operation': 'marketplace.promotion_check', 'action': 'offer', 'safe_to_retry': True})
    assert [record.levelname for record in caplog.records] == ['ERROR', 'ERROR']


def test_hosted_cooldown_read_preserves_evidence_at_info(monkeypatch, caplog):
    from vendoo_studio.services.hosted_browser_relay import BrowserRelay
    relay = BrowserRelay()
    result = _cooldown_reply()
    request = AsyncMock(return_value=result)
    monkeypatch.setattr(relay, '_read', request)
    with caplog.at_level(logging.INFO, logger='vendoo_studio.marketplaces'):
        assert asyncio.run(relay.read('marketplace.promotion_check', {'marketplace': 'mercari', 'action': 'offer'}, 90)) is result
    assert [record.levelname for record in caplog.records] == ['INFO']
    assert result['marketplace_message'] in caplog.text
    request.assert_awaited_once()
