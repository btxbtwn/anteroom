import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from vendoo_studio.services.native_editor import vinted_brands
from vendoo_studio.services.native_marketplaces import NativeMarketplaceError


def test_brand_search_forwards_read_only_query():
    with patch('vendoo_studio.services.native_editor.ConversationRepo') as repo, \
            patch('vendoo_studio.services.native_editor.browser_bridge.request',
                  new_callable=AsyncMock, return_value={'ok': True, 'options': ['Nike']}) as request:
        repo.return_value.get.return_value = SimpleNamespace(id='conversation')
        result = asyncio.run(vinted_brands('conversation', 'Ni', 'Women > Shoes', Mock()))
        assert result == {'options': ['Nike']}
        assert request.call_args.args[1:] == ('marketplace.vinted_brands',
                                               {'query': 'Ni', 'category': 'Women > Shoes'})


def test_brand_search_surfaces_marketplace_error():
    with patch('vendoo_studio.services.native_editor.ConversationRepo'), \
            patch('vendoo_studio.services.native_editor.browser_bridge.request',
                  new_callable=AsyncMock, return_value={'ok': False, 'error': 'Reconnect Vinted'}):
        with pytest.raises(NativeMarketplaceError):
            asyncio.run(vinted_brands('conversation', 'Ni', '', Mock()))
