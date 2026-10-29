import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from vendoo_studio.services.native_editor import vinted_categories
from vendoo_studio.services.native_marketplaces import NativeMarketplaceError


def test_categories_are_read_through_chrome():
    with patch('vendoo_studio.services.native_editor.ConversationRepo') as repo, \
            patch('vendoo_studio.services.native_editor.browser_bridge.request',
                  new_callable=AsyncMock,
                  return_value={'ok': True, 'options': ['Women > Clothing > Jeans', 'Men > Clothing > Jeans']}) as request:
        repo.return_value.get.return_value = SimpleNamespace(id='conversation')
        result = asyncio.run(vinted_categories('conversation', Mock()))
        assert result == {'options': ['Women > Clothing > Jeans', 'Men > Clothing > Jeans']}
        assert request.call_args.args[1:] == ('marketplace.vinted_categories', {})


def test_category_read_surfaces_marketplace_error():
    with patch('vendoo_studio.services.native_editor.ConversationRepo'), \
            patch('vendoo_studio.services.native_editor.browser_bridge.request',
                  new_callable=AsyncMock, return_value={'ok': False, 'error': 'Reconnect Vinted'}):
        with pytest.raises(NativeMarketplaceError):
            asyncio.run(vinted_categories('conversation', Mock()))
