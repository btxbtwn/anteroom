from unittest.mock import AsyncMock

import pytest

import test_ebay_import
import test_etsy_import

from vendoo_studio.models.listing import ListingRevision
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
from vendoo_studio.repositories.queries import ListingRepo
from vendoo_studio.services import marketplace_inventory

ITEM_ID = test_ebay_import.ITEM_ID
ebay_imported = test_ebay_import.imported
etsy_imported = test_etsy_import.imported


def import_item(fixture, marketplace):
    identifier = ITEM_ID if marketplace == "ebay" else "555"
    response = fixture.client.post(f"/api/{marketplace}/import", json={"listing_id": identifier})
    assert response.status_code == 200, response.text
    return response.json()["conversation_id"]


def sync(fixture, conv_id):
    response = fixture.client.post(f"/api/conversations/{conv_id}/native-marketplaces/sync")
    assert response.status_code == 200, response.text
    return response.json()["marketplaces"]


def test_etsy_sync_reports_exact_remote_differences_without_overwriting_edits(etsy_imported):
    fixture = etsy_imported
    conv_id = import_item(fixture, "etsy")
    ListingRepo(fixture.db).save_revision(conv_id, {"title": "My coat", "price": 99, "quantity": 8}, "user")
    outcome = sync(fixture, conv_id)["etsy"]
    assert outcome["ok"] is True
    assert outcome["inventory"]["state"] == "active"
    assert outcome["inventory"]["price"] == 45.5
    assert outcome["inventory"]["quantity"] == 3
    assert outcome["inventory"]["differences"] == [
        {"field": "price", "local": 99, "remote": 45.5}, {"field": "quantity", "local": 8, "remote": 3}]
    assert fixture.db.query(ListingRevision).count() == 2
    assert ListingRepo(fixture.db).get_revisions(conv_id)[0].listing_json["price"] == 99
    assert fixture.db.query(MarketplaceSale).count() == 0
    assert len(fixture.remote.downloads) == 2


def test_ebay_sync_subtracts_sold_quantity_and_retains_local_revision(ebay_imported, monkeypatch):
    fixture = ebay_imported
    monkeypatch.setattr(marketplace_inventory, "assert_identity", lambda _: None)
    conv_id = import_item(fixture, "ebay")
    outcome = sync(fixture, conv_id)["ebay"]
    assert outcome["ok"] and outcome["inventory"]["quantity"] == 3
    assert outcome["inventory"]["differences"] == []
    assert fixture.db.query(ListingRevision).count() == 1
    assert len(fixture.downloads) == 2


@pytest.mark.parametrize("state", ["inactive", "sold_out", "expired", "removed", "draft"])
def test_inactive_etsy_status_is_observed_without_inventing_a_sale(etsy_imported, state):
    conv_id = import_item(etsy_imported, "etsy")
    etsy_imported.remote.item["state"] = state
    etsy_imported.remote.calls.clear()
    outcome = sync(etsy_imported, conv_id)["etsy"]
    assert outcome["ok"] and outcome["inventory"]["state"] == state
    assert outcome["inventory"]["quantity"] is None
    assert etsy_imported.db.query(MarketplaceSale).count() == 0
    assert etsy_imported.remote.calls == [("listings/555", {"includes": "Images"})]
    assert MarketplaceListingRepo(etsy_imported.db).get(conv_id, "etsy")["state"] == "published"


@pytest.mark.parametrize("state", ["Ended", "Completed"])
def test_ended_ebay_is_observed_without_a_sale_or_delist(ebay_imported, monkeypatch, state):
    monkeypatch.setattr(marketplace_inventory, "assert_identity", lambda _: None)
    conv_id = import_item(ebay_imported, "ebay")
    ebay_imported.remote.item.find("SellingStatus/ListingStatus").text = state
    outcome = sync(ebay_imported, conv_id)["ebay"]
    assert outcome["ok"] and outcome["inventory"]["state"] == state.lower()
    assert outcome["inventory"]["quantity"] is None
    assert ebay_imported.db.query(MarketplaceSale).count() == 0


@pytest.mark.parametrize("key,value", [("shop_id", 99), ("listing_id", 556), ("state", "unexpected")])
def test_etsy_unowned_or_unreliable_read_is_not_a_success(etsy_imported, key, value):
    conv_id = import_item(etsy_imported, "etsy")
    etsy_imported.remote.item[key] = value
    outcome = sync(etsy_imported, conv_id)["etsy"]
    assert not outcome["ok"] and outcome["error"]
    assert "inventory" not in outcome


def test_changed_ebay_owner_is_rejected(ebay_imported, monkeypatch):
    monkeypatch.setattr(marketplace_inventory, "assert_identity", lambda _: None)
    conv_id = import_item(ebay_imported, "ebay")
    ebay_imported.remote.item.find("Seller/UserID").text = "someone-else"
    outcome = sync(ebay_imported, conv_id)["ebay"]
    assert not outcome["ok"] and "connected eBay seller" in outcome["error"]


def test_account_change_during_etsy_read_discards_the_observation(etsy_imported):
    conv_id = import_item(etsy_imported, "etsy")
    etsy_imported.remote.after_read = lambda: etsy_imported.remote.accounts["etsy"].update(connectedAt=2)
    outcome = sync(etsy_imported, conv_id)["etsy"]
    assert not outcome["ok"] and "connection changed" in outcome["error"]


@pytest.mark.parametrize("mutation", ["variations", "currency", "multiple-offerings"])
def test_unreliable_etsy_inventory_keeps_status_with_an_explicit_warning(etsy_imported, mutation):
    conv_id = import_item(etsy_imported, "etsy")
    if mutation == "variations":
        etsy_imported.remote.item["has_variations"] = True
    elif mutation == "currency":
        etsy_imported.remote.offering["price"]["currency_code"] = "EUR"
    else:
        etsy_imported.remote.product["offerings"].append(dict(etsy_imported.remote.offering))
    outcome = sync(etsy_imported, conv_id)["etsy"]
    assert outcome["ok"] and outcome["inventory"]["state"] == "active"
    assert outcome["inventory"]["price"] is None and outcome["inventory"]["quantity"] is None
    assert outcome["inventory"]["warning"]


def test_replaced_link_during_a_read_discards_stale_values(etsy_imported):
    conv_id = import_item(etsy_imported, "etsy")
    etsy_imported.remote.after_read = lambda: MarketplaceListingRepo(etsy_imported.db).save(
        conv_id, "etsy", {"listing_url": "https://www.etsy.com/listing/556"})
    outcome = sync(etsy_imported, conv_id)["etsy"]
    assert not outcome["ok"] and "link or status changed" in outcome["error"]


def test_one_failed_marketplace_does_not_hide_another_success(etsy_imported, monkeypatch):
    conv_id = import_item(etsy_imported, "etsy")
    MarketplaceListingRepo(etsy_imported.db).save(conv_id, "ebay", {
        "state": "published", "listing_url": f"https://www.ebay.com/itm/{ITEM_ID}"})
    monkeypatch.setattr(marketplace_inventory, "_ebay", AsyncMock(side_effect=RuntimeError("fixture-token private response")))
    outcome = sync(etsy_imported, conv_id)
    assert outcome["etsy"]["ok"] and not outcome["ebay"]["ok"]
    assert "fixture-token" not in str(outcome)


def test_running_marketplace_action_is_not_read_concurrently(etsy_imported, monkeypatch):
    from vendoo_studio.services import native_marketplaces

    conv_id = import_item(etsy_imported, "etsy")
    monkeypatch.setattr(native_marketplaces, "_running", lambda _: True)
    etsy_imported.remote.calls.clear()
    outcome = sync(etsy_imported, conv_id)["etsy"]
    assert not outcome["ok"] and "action is running" in outcome["error"]
    assert etsy_imported.remote.calls == []
