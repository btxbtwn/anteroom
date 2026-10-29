from unittest.mock import MagicMock, Mock, patch

import pytest

from vendoo_studio.services import sellwild_browser as browser
from vendoo_studio.services.account_browser import AccountConnectionError


def test_only_the_three_native_account_reads_are_performed_and_failures_are_explicit():
    with patch.object(browser._session, 'read', side_effect=[
        [{'ship_from': 'Seller address'}], AccountConnectionError('unavailable'),
        [{'id': 117, 'name': '25OFF', 'discountType': 'percentage', 'discountValue': 25}],
    ]) as read:
        result = browser.account_choices()
    assert result == {'ship_from': 'Seller address', 'default_parcel': '', 'unavailable': ['parcel'],
                      'coupons': [{'id': 117, 'name': '25OFF', 'discountType': 'percentage', 'discountValue': 25}]}
    assert [call.args[0] for call in read.call_args_list] == [
        '/api/addresses', '/api/user/profile', '/api/seller/coupons?limit=50']
    # Full profiles and cookies never become return values.
    assert 'body.profile?.defaultParcelTemplate' in read.call_args_list[1].args[1]
    assert 'document.cookie' not in read.call_args_list[1].args[1]


def test_missing_session_leaves_account_choices_unavailable_without_chrome():
    with patch.object(browser._session, '_window', None):
        result = browser.account_choices()
    assert result == {'ship_from': None, 'coupons': [], 'default_parcel': '',
                      'unavailable': ['address', 'parcel', 'coupons']}


@pytest.mark.parametrize('path', ['https://example.com/api/addresses', '//example.com/api/addresses'])
def test_account_transport_rejects_external_endpoints(path):
    with pytest.raises(ValueError):
        browser._session.read(path, '[]')


def test_sellwild_connect_requires_anteroom_and_opens_only_its_sell_page():
    item = MagicMock()
    factory = Mock(return_value=item)
    with patch.object(browser._session, '_factory', factory), patch.object(browser._session, '_window', None):
        assert browser.connect() == {'ok': True}
        assert factory.call_args.args == ('Sign in to Sellwild', 'https://sellwild.com/sell')
        assert factory.call_args.kwargs['js_api'] is None
        browser.shutdown()
