import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from vendoo_studio.services.native_editor import vinted_brands
from vendoo_studio.services.native_marketplaces import NativeMarketplaceError
from vendoo_studio.services.vinted_browser import VintedConnectionError


def test_brands_use_anteroom_reader_without_chrome():
    with patch('vendoo_studio.services.native_editor.ConversationRepo'), \
         patch('vendoo_studio.services.vinted_browser.brands', return_value=['Nike']) as read, \
         patch('vendoo_studio.services.browser_bridge.request', new_callable=AsyncMock) as chrome:
        result = asyncio.run(vinted_brands('conversation', 'Ni', 'Women > Shoes', Mock()))
        assert result == {'options': ['Nike']}
        read.assert_called_once()
        chrome.assert_not_awaited()


def test_brands_require_native_sign_in_when_unavailable():
    with patch('vendoo_studio.services.native_editor.ConversationRepo'), \
         patch('vendoo_studio.services.vinted_browser.brands', side_effect=VintedConnectionError('Sign in to Vinted')):
        with pytest.raises(NativeMarketplaceError) as error:
            asyncio.run(vinted_brands('conversation', 'Ni', 'Women > Shoes', Mock()))
        assert error.value.status_code == 409
