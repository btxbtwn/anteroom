from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from vendoo_studio.services import vinted_browser as browser


@pytest.fixture(autouse=True)
def isolated_browser():
    with patch.object(browser._session, '_factory', None), patch.object(browser._session, '_window', None):
        yield


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self):
        return all(handler() is not False for handler in self.handlers)


def window(rows):
    item = Mock()
    item.events = SimpleNamespace(closed=Event(), closing=Event())
    item.get_current_url.return_value = 'https://www.vinted.com/items/new'
    item.evaluate_js.side_effect = lambda script, callback: callback({'ok': True, 'rows': rows})
    return item


def test_connection_opens_anteroom_window_without_exposing_python_api():
    item = window([])
    factory = Mock(return_value=item)
    browser.install(factory)
    assert browser.connect() == {'ok': True}
    assert factory.call_args.kwargs['js_api'] is None
    assert factory.call_args.args[1] == 'https://www.vinted.com/items/new'
    browser.connect()
    factory.assert_called_once()
    item.show.assert_called_once()
    assert item.events.closing.fire() is False
    item.hide.assert_called_once()
    browser.shutdown()
    assert item.events.closing.fire() is True
    item.destroy.assert_called_once()
    assert browser._session._window is None


def test_headless_server_does_not_open_chrome_or_fabricate_a_connection():
    with pytest.raises(browser.VintedConnectionError, match='Mac app'):
        browser.connect()


def test_catalog_read_is_same_origin_get_and_hides_the_signed_in_window():
    item = window([{'id': 1, 'title': 'Women', 'catalogs': [{'id': 2, 'title': 'Jeans'}]}])
    browser._session._window = item
    assert browser.category_options(browser.category_rows()) == ['Women > Jeans']
    script = item.evaluate_js.call_args.args[0]
    assert 'method:\'GET\'' in script
    assert 'credentials:\'same-origin\'' in script
    assert '"/api/v2/item_upload/catalogs"' in script
    assert 'AbortController' in script
    item.hide.assert_called_once()


@pytest.mark.parametrize('url', ['http://www.vinted.com', 'https://www.vinted.com.evil.test', 'https://example.com'])
def test_redirected_sign_in_window_cannot_be_used_as_a_cross_origin_reader(url):
    item = window([])
    item.get_current_url.return_value = url
    browser._session._window = item
    with pytest.raises(browser.VintedConnectionError):
        browser.category_rows()
    item.evaluate_js.assert_not_called()


def test_read_refuses_listing_and_account_endpoints():
    for path in ['/api/v2/users/current', '/api/v2/item_upload/drafts', 'https://example.com/api/v2/item_upload/catalogs']:
        with pytest.raises(ValueError):
            browser._read(path, 'catalogs')


def test_browser_errors_are_generic_and_never_return_credentials_or_raw_errors():
    item = window([])
    item.evaluate_js.side_effect = RuntimeError('private-session-value')
    browser._session._window = item
    with pytest.raises(browser.VintedConnectionError) as error:
        browser.category_rows()
    assert 'private-session-value' not in str(error.value)


def test_brand_search_resolves_exact_native_category_and_encodes_the_query():
    browser._session._window = window([{'id': 9, 'title': 'Nike'}])
    with patch.object(browser, 'category_rows', return_value=[{'id': 4, 'title': 'Women', 'catalogs': [{'id': 8, 'title': 'Shoes'}]}]):
        assert browser.brands('Ni & co', 'Women > Shoes') == ['Nike']
    script = browser._session._window.evaluate_js.call_args.args[0]
    assert 'keyword=Ni+%26+co&category_id=8' in script


def test_unrecognised_category_does_not_issue_an_unscoped_brand_search():
    browser._session._window = window([])
    with patch.object(browser, 'category_rows', return_value=[]), pytest.raises(browser.VintedConnectionError):
        browser.brands('Ni', 'Invented > Shoes')
    browser._session._window.evaluate_js.assert_not_called()


def test_closed_window_errors_do_not_leak_native_browser_details():
    item = window([])
    item.get_current_url.side_effect = RuntimeError('private-native-browser-details')
    browser._session._window = item
    with pytest.raises(browser.VintedConnectionError) as error:
        browser.category_rows()
    assert 'private-native-browser-details' not in str(error.value)
    item.evaluate_js.assert_not_called()
