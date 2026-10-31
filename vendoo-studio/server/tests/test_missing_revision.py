from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from vendoo_studio.repositories.queries import ListingRepo
from vendoo_studio.services import native_marketplaces


def test_local_sellwild_save_refuses_a_missing_revision():
    body = native_marketplaces.ListRequest(revision_id='missing')
    with patch.object(native_marketplaces, '_conversation'), \
         patch.object(ListingRepo, 'get_current', return_value=SimpleNamespace(current_revision_id='missing')), \
         patch.object(ListingRepo, 'get_revision', return_value=None):
        with pytest.raises(native_marketplaces.NativeMarketplaceError) as caught:
            native_marketplaces.save_local_sellwild_draft('draft', body, MagicMock())
    assert caught.value.status_code == 409
