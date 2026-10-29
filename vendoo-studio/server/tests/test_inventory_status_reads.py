from unittest.mock import Mock, patch

import pytest

from vendoo_studio.services.item_status import statuses
from vendoo_studio.services.marketplace_delist import removed_marketplaces


def test_empty_status_batch_does_not_query_database():
    db = Mock()
    assert statuses(db, []) == {}
    db.query.assert_not_called()


@pytest.mark.parametrize("marks,delists", [
    ({}, {}),
    ({"ebay": {"state": "listed"}}, {}),
    ({"ebay": {"state": "sold"}}, {"ebay": {"state": "failed"}}),
    ({"ebay": "invalid"}, {"ebay": None}),
])
def test_inventory_does_not_resolve_urls_without_removal_records(marks, delists):
    notes = {"marketplaceMarks": marks, "marketplaceDelists": delists,
             "vendooListingUrls": {"ebay": "https://www.ebay.com/itm/123456789012"}}
    with patch("vendoo_studio.services.marketplace_delist.current_listing") as resolve:
        assert removed_marketplaces(notes, {}) == []
        resolve.assert_not_called()


@pytest.mark.parametrize("record_key,state", [
    ("marketplaceMarks", "not_listed"), ("marketplaceDelists", "deleted"),
])
@pytest.mark.parametrize("current_id,expected", [
    (None, ["ebay"]), ("123456789012", ["ebay"]), ("987654321098", []),
])
def test_removal_applies_only_to_the_removed_listing(record_key, state, current_id, expected):
    notes = {record_key: {"ebay": {"state": state, "listing_id": "123456789012"}}}
    urls = {"ebay": f"https://www.ebay.com/itm/{current_id}"} if current_id else {}
    assert removed_marketplaces(notes, urls) == expected


def test_new_seller_listing_supersedes_old_delete():
    notes = {
        "marketplaceMarks": {"ebay": {"state": "listed", "url": "https://www.ebay.com/itm/987654321098"}},
        "marketplaceDelists": {"ebay": {"state": "deleted", "listing_id": "123456789012"}},
        "vendooListingUrls": {"ebay": "https://www.ebay.com/itm/123456789012"},
    }
    assert removed_marketplaces(notes, {}) == []
