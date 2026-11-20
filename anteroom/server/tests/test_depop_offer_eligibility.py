"""Only the local multi-size preview guard is an expected refusal."""
import asyncio
from copy import deepcopy
import logging
from unittest.mock import AsyncMock

import pytest

from anteroom.models.listing_promotion import ListingPromotion
from anteroom.services import browser_bridge, listing_promotions
from anteroom.services.marketplace_errors import report_result

MESSAGE = 'Depop does not send offers on listings with several sizes.'
BASE = {'ok': False, 'error': MESSAGE, 'error_code': 'rejected', 'safe_to_retry': True}


def test_multisize_guard_stays_visible_without_operator_errors_at_either_layer(monkeypatch, caplog):
    reply = deepcopy(BASE)
    request = AsyncMock(return_value=reply)
    monkeypatch.setattr(browser_bridge, '_request', request)
    row = ListingPromotion(marketplace='depop', action='offer', batch_id='fixture', discount_percent=10)
    with caplog.at_level(logging.INFO), pytest.raises(listing_promotions.PromotionError) as raised:
        asyncio.run(listing_promotions._review_remote(row, 'fixture-slug'))
    records = [r for r in caplog.records if r.name in ('anteroom.marketplaces', 'anteroom.listing_promotions')]
    assert [r.levelname for r in records] == ['INFO', 'INFO']
    assert all(MESSAGE in r.getMessage() for r in records)
    assert str(raised.value) == MESSAGE
    assert raised.value.code == 'rejected'
    assert row.offer_price_cents is None
    assert reply == BASE
    request.assert_awaited_once()


@pytest.mark.parametrize('override', [
    {'safe_to_retry': False}, {'safe_to_retry': None}, {'safe_to_retry': 1},
    {'error_code': 'uncertain'}, {'error_code': 'signed_out'}, {'error_code': 'schema_drift'},
    {'error': MESSAGE + ' extra'}, {'state': 'unknown'}, {'state': 'sending'},
    {'errors': [{'message': 'Other failure'}]}, {'statusCode': 404}, {'marketplace_message': 'Product not found'},
    {'marketplace': 'mercari'}, {'action': 'publish'}, {'operation': 'marketplace.promote'},
])
def test_other_failure_indicators_keep_both_layers_at_error(monkeypatch, caplog, override):
    request = AsyncMock(return_value={**BASE, **override})
    monkeypatch.setattr(browser_bridge, '_request', request)
    row = ListingPromotion(marketplace='depop', action='offer', batch_id='fixture', discount_percent=10)
    with caplog.at_level(logging.INFO), pytest.raises(listing_promotions.PromotionError):
        asyncio.run(listing_promotions._review_remote(row, 'fixture-slug'))
    assert [r.levelname for r in caplog.records if r.name in (
        'anteroom.marketplaces', 'anteroom.listing_promotions')] == ['ERROR', 'ERROR']
    request.assert_awaited_once()


@pytest.mark.parametrize('marketplace,operation,action', [
    ('mercari', 'marketplace.promotion_check', 'offer'), ('depop', 'marketplace.promote', 'offer'),
    ('depop', 'marketplace.promotion_result', 'offer'), ('depop', 'marketplace.promotion_check', 'share'),
    ('depop', 'marketplace.promotion_check', None),
])
def test_words_outside_depop_offer_preview_remain_errors(caplog, marketplace, operation, action):
    with caplog.at_level(logging.INFO):
        report_result(marketplace, operation, BASE, context={'action': action})
    assert [r.levelname for r in caplog.records] == ['ERROR']


def test_nested_refusal_and_unknown_send_do_not_inherit_expected_classification(caplog):
    result = {**BASE, 'results': [deepcopy(BASE), {'state': 'unknown', 'error': 'Unconfirmed send'}]}
    with caplog.at_level(logging.INFO):
        report_result('depop', 'marketplace.promotion_check', result, context={'action': 'offer'})
    assert [r.levelname for r in caplog.records] == ['INFO', 'ERROR', 'ERROR']


def test_hosted_multisize_preview_keeps_reply_and_info_evidence(monkeypatch, caplog):
    from anteroom.services.hosted_browser_relay import BrowserRelay
    relay = BrowserRelay()
    reply = deepcopy(BASE)
    request = AsyncMock(return_value=reply)
    monkeypatch.setattr(relay, '_read', request)
    with caplog.at_level(logging.INFO):
        assert asyncio.run(relay.read('marketplace.promotion_check', {'marketplace': 'depop', 'action': 'offer'}, 90)) is reply
    assert [r.levelname for r in caplog.records] == ['INFO']
    assert MESSAGE in caplog.text
    assert reply == BASE
    request.assert_awaited_once()


NO_RECIPIENTS_MESSAGE = (
    'Depop has nobody to send an offer to on this listing: '
    'no one has liked it, or every liker already has an offer.'
)
NO_RECIPIENTS_BASE = {
    'ok': False,
    'like_count': 0,
    'error': NO_RECIPIENTS_MESSAGE,
    'error_code': 'rejected',
    'safe_to_retry': True,
}


def test_no_recipients_preview_logs_info_at_both_layers_and_records_zero_likes(monkeypatch, caplog):
    reply = deepcopy(NO_RECIPIENTS_BASE)
    request = AsyncMock(return_value=reply)
    monkeypatch.setattr(browser_bridge, '_request', request)
    row = ListingPromotion(marketplace='depop', action='offer', batch_id='fixture', discount_percent=10)
    with caplog.at_level(logging.INFO), pytest.raises(listing_promotions.PromotionError) as raised:
        asyncio.run(listing_promotions._review_remote(row, 'fixture-slug'))
    records = [r for r in caplog.records if r.name in ('anteroom.marketplaces', 'anteroom.listing_promotions')]
    assert [r.levelname for r in records] == ['INFO', 'INFO']
    assert all(NO_RECIPIENTS_MESSAGE in r.getMessage() for r in records)
    assert str(raised.value) == NO_RECIPIENTS_MESSAGE
    assert raised.value.code == 'rejected'
    assert row.like_count == 0
    assert row.offer_price_cents is None
    assert reply == NO_RECIPIENTS_BASE
    request.assert_awaited_once()


@pytest.mark.parametrize('override', [
    {'like_count': 1}, {'like_count': None}, {'like_count': False}, {'like_count': '0'},
    {'safe_to_retry': False}, {'safe_to_retry': None},
    {'error_code': 'uncertain'}, {'error_code': 'schema_drift'},
    {'error': NO_RECIPIENTS_MESSAGE + ' extra'}, {'state': 'unknown'},
    {'errors': [{'message': 'Other failure'}]}, {'statusCode': 400},
    {'marketplace_message': 'Does the product have variants?'},
    {'marketplace': 'mercari'}, {'action': 'share'}, {'operation': 'marketplace.promote'},
])
def test_no_recipients_with_other_failure_indicators_stays_error(monkeypatch, caplog, override):
    request = AsyncMock(return_value={**NO_RECIPIENTS_BASE, **override})
    monkeypatch.setattr(browser_bridge, '_request', request)
    row = ListingPromotion(marketplace='depop', action='offer', batch_id='fixture', discount_percent=10)
    with caplog.at_level(logging.INFO), pytest.raises(listing_promotions.PromotionError):
        asyncio.run(listing_promotions._review_remote(row, 'fixture-slug'))
    assert [r.levelname for r in caplog.records if r.name in (
        'anteroom.marketplaces', 'anteroom.listing_promotions')] == ['ERROR', 'ERROR']
    request.assert_awaited_once()


def test_no_recipients_missing_like_count_stays_error(caplog):
    reply = {k: v for k, v in NO_RECIPIENTS_BASE.items() if k != 'like_count'}
    with caplog.at_level(logging.INFO):
        report_result('depop', 'marketplace.promotion_check', reply, context={'action': 'offer'})
    assert [r.levelname for r in caplog.records] == ['ERROR']


def test_nested_no_recipients_does_not_inherit_expected_classification(caplog):
    result = {**NO_RECIPIENTS_BASE, 'results': [deepcopy(NO_RECIPIENTS_BASE)]}
    with caplog.at_level(logging.INFO):
        report_result('depop', 'marketplace.promotion_check', result, context={'action': 'offer'})
    assert [r.levelname for r in caplog.records] == ['INFO', 'ERROR']


def test_hosted_no_recipients_preview_keeps_reply_and_info_evidence(monkeypatch, caplog):
    from anteroom.services.hosted_browser_relay import BrowserRelay
    relay = BrowserRelay()
    reply = deepcopy(NO_RECIPIENTS_BASE)
    request = AsyncMock(return_value=reply)
    monkeypatch.setattr(relay, '_read', request)
    with caplog.at_level(logging.INFO):
        assert asyncio.run(relay.read('marketplace.promotion_check', {'marketplace': 'depop', 'action': 'offer'}, 90)) is reply
    assert [r.levelname for r in caplog.records] == ['INFO']
    assert NO_RECIPIENTS_MESSAGE in caplog.text
    assert reply == NO_RECIPIENTS_BASE
    request.assert_awaited_once()
