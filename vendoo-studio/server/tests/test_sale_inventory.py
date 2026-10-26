from datetime import timedelta

import pytest
import test_marketplace_inventory as inventory_tests

from vendoo_studio.models.conversation import utcnow
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
from vendoo_studio.repositories.queries import ListingRepo
from vendoo_studio.services.sale_inventory import stock_after_sale

etsy_imported = inventory_tests.etsy_imported


def prepare(fixture):
    conv_id = inventory_tests.import_item(fixture, "etsy")
    sale = MarketplaceSale(marketplace="etsy", listing_id="555", order_id="order", line_id="line", quantity=1, conversation_id=conv_id)
    fixture.db.add(sale)
    fixture.db.commit()
    return conv_id, sale


def stock(fixture, conv_id):
    return stock_after_sale(fixture.db, conv_id, "etsy", "555")


def test_partial_sale_requires_fresh_stock_instead_of_subtracting_from_local_edits(etsy_imported):
    conv_id, _ = prepare(etsy_imported)
    assert stock(etsy_imported, conv_id) == {"status": "unknown", "remaining": None}
    result = etsy_imported.client.get(f"/api/conversations/{conv_id}/marketplace-listings").json()
    assert result["etsy"]["sold_here"] is False
    inventory_tests.sync(etsy_imported, conv_id)
    assert stock(etsy_imported, conv_id) == {"status": "remaining", "remaining": 3}
    alert = etsy_imported.client.get("/api/marketplace-sales").json()["sales"][0]
    assert (alert["stockStatus"], alert["remainingQuantity"]) == ("remaining", 3)
    assert ListingRepo(etsy_imported.db).get_revisions(conv_id)[0].listing_json["quantity"] == 3


def test_a_new_sale_invalidates_the_previous_stock_read(etsy_imported):
    conv_id, _ = prepare(etsy_imported)
    inventory_tests.sync(etsy_imported, conv_id)
    etsy_imported.db.add(MarketplaceSale(marketplace="etsy", listing_id="555", order_id="second", line_id="two", quantity=1, conversation_id=conv_id))
    etsy_imported.db.commit()
    assert stock(etsy_imported, conv_id)["status"] == "unknown"


@pytest.mark.parametrize("state,quantity", [("active", 0), ("sold_out", 3)])
def test_verified_exhausted_stock_is_distinguished_from_partial_sales(etsy_imported, state, quantity):
    conv_id, _ = prepare(etsy_imported)
    etsy_imported.remote.item["state"] = state
    etsy_imported.remote.offering["quantity"] = quantity
    inventory_tests.sync(etsy_imported, conv_id)
    assert stock(etsy_imported, conv_id) == {"status": "sold_out", "remaining": 0}
    listings = etsy_imported.client.get(f"/api/conversations/{conv_id}/marketplace-listings").json()
    assert listings["etsy"]["sold_here"] is True


def test_ended_listing_is_not_proof_multiunit_stock_is_exhausted(etsy_imported):
    conv_id, _ = prepare(etsy_imported)
    etsy_imported.remote.item["state"] = "inactive"
    inventory_tests.sync(etsy_imported, conv_id)
    assert stock(etsy_imported, conv_id)["status"] == "unknown"


def test_single_item_sale_can_be_checked_again_to_detect_remaining_stock(etsy_imported):
    conv_id, _ = prepare(etsy_imported)
    ListingRepo(etsy_imported.db).save_revision(conv_id, {"title": "Coat", "quantity": 1}, "user")
    assert stock(etsy_imported, conv_id)["status"] == "sold_out"
    result = inventory_tests.sync(etsy_imported, conv_id)
    assert result["etsy"]["ok"]
    assert stock(etsy_imported, conv_id) == {"status": "remaining", "remaining": 3}


def test_stock_read_started_before_sale_receipt_is_stale_even_if_it_finished_later(etsy_imported):
    conv_id, sale = prepare(etsy_imported)
    MarketplaceListingRepo(etsy_imported.db).save(conv_id, "etsy", {"inventory_observation": {
        "listing_id": "555", "state": "active", "quantity": 3,
        "checked_at": (sale.received_at - timedelta(seconds=1)).isoformat()}})
    assert stock(etsy_imported, conv_id)["status"] == "unknown"


def test_replacing_listing_id_invalidates_its_old_observation(etsy_imported):
    conv_id, _ = prepare(etsy_imported)
    inventory_tests.sync(etsy_imported, conv_id)
    MarketplaceListingRepo(etsy_imported.db).save(conv_id, "etsy", {"listing_url": "https://www.etsy.com/listing/556"})
    assert stock(etsy_imported, conv_id)["status"] == "unknown"


def test_partial_stock_is_not_automatically_delisted_even_if_seller_enabled_the_setting(etsy_imported, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from vendoo_studio.services import marketplace_accounts, marketplace_delist, user_settings

    conv_id, sale = prepare(etsy_imported)
    monkeypatch.setattr(user_settings, "autodelist_enabled", lambda: True)
    delete = AsyncMock()
    monkeypatch.setattr(marketplace_delist, "delist", delete)
    result = asyncio.run(marketplace_accounts.autodelist_for_sales([sale.id], session_factory=lambda: etsy_imported.db))
    assert "Stock remains or could not be verified" in result[sale.id]
    delete.assert_not_called()


def test_observation_contains_only_public_inventory_fields(etsy_imported):
    conv_id, _ = prepare(etsy_imported)
    inventory_tests.sync(etsy_imported, conv_id)
    saved = MarketplaceListingRepo(etsy_imported.db).get(conv_id, "etsy")["inventory_observation"]
    assert set(saved) == {"listing_id", "checked_at", "state", "quantity", "price"}
    assert "fixture-token" not in str(saved)
    assert saved["checked_at"] <= utcnow().isoformat() + "+00:00"
