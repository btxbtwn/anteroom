from unittest.mock import Mock, patch

import pytest

from vendoo_studio.services.item_status import statuses
from vendoo_studio.services.marketplace_delist import known_listings, listing_id, removed_marketplaces


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


def test_a_new_direct_receipt_supersedes_the_deleted_seller_link():
    notes = {
        "marketplaceMarks": {"ebay": {"state": "listed", "url": "https://www.ebay.com/itm/123456789012"}},
        "marketplaceDelists": {"ebay": {"state": "deleted", "listing_id": "123456789012"}},
    }
    assert removed_marketplaces(notes, {"ebay": "https://www.ebay.com/itm/987654321098"}) == []


@pytest.mark.parametrize("receipt", [None, "https://www.ebay.com/itm/123456789012", "https://invalid.example/987654321098"])
def test_an_absent_same_or_invalid_receipt_does_not_revive_a_deleted_seller_link(receipt):
    notes = {
        "marketplaceMarks": {"ebay": {"state": "listed", "url": "https://www.ebay.com/itm/123456789012"}},
        "marketplaceDelists": {"ebay": {"state": "deleted", "listing_id": "123456789012"}},
    }
    assert removed_marketplaces(notes, {"ebay": receipt} if receipt else {}) == ["ebay"]


@pytest.mark.parametrize("marketplace,old,new", [
    ("ebay", "https://www.ebay.com/itm/123456789012", "https://www.ebay.com/itm/987654321098"),
    ("etsy", "https://www.etsy.com/listing/1234567890/fixture", "https://www.etsy.com/listing/9876543210/fixture"),
    ("poshmark", "https://poshmark.com/listing/aaaaaaaaaaaaaaaaaaaaaaaa", "https://poshmark.com/listing/bbbbbbbbbbbbbbbbbbbbbbbb"),
    ("mercari", "https://www.mercari.com/us/item/m12345678901/", "https://www.mercari.com/us/item/m98765432109/"),
    ("depop", "https://www.depop.com/products/fixture-old-123/", "https://www.depop.com/products/fixture-new-456/"),
])
def test_receipt_identity_agrees_in_detail_and_inventory_without_rewriting_notes(marketplace, old, new):
    import copy

    notes = {"marketplaceMarks": {marketplace: {"state": "listed", "url": old}},
             "marketplaceDelists": {marketplace: {"state": "deleted", "listing_id": listing_id(marketplace, old)}}}
    before = copy.deepcopy(notes)
    live = known_listings(notes, {marketplace: {"state": "published", "listing_url": new}}, {})[marketplace]
    assert live["state"] == "live" and live["url"] == new and live["source"] == "anteroom"
    assert live["listing_id"] == listing_id(marketplace, new)
    assert removed_marketplaces(notes, {marketplace: new}) == []
    assert notes == before


def test_a_seller_correction_without_a_matching_delete_keeps_priority():
    old = "https://poshmark.com/listing/aaaaaaaaaaaaaaaaaaaaaaaa"
    correction = "https://poshmark.com/listing/bbbbbbbbbbbbbbbbbbbbbbbb"
    notes = {"marketplaceMarks": {"poshmark": {"state": "listed", "url": correction}}}
    live = known_listings(notes, {"poshmark": {"state": "published", "listing_url": old}}, {})["poshmark"]
    assert live["state"] == "live" and live["url"] == correction and live["source"] == "seller"
