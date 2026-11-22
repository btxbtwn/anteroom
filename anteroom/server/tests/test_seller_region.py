from unittest.mock import patch

import pytest

from anteroom.services import marketplaces
from anteroom.services.ebay_listings import FIXED_PRICE, _offer_body
from anteroom.services.seller_region import seller_region


@pytest.mark.parametrize(("code", "currency", "site", "available"), [
    ("US", "USD", "EBAY_US", {"ebay", "etsy", "depop", "poshmark", "mercari"}),
    ("CA", "CAD", "EBAY_CA", {"ebay", "etsy", "depop", "poshmark", "mercari"}),
    ("GB", "GBP", "EBAY_GB", {"ebay", "etsy", "depop"}),
    ("AU", "AUD", "EBAY_AU", {"ebay", "etsy", "depop"}),
])
def test_region_controls_marketplace_selection_and_ebay_offer(code, currency, site, available):
    settings = {"seller_region": code}
    region = seller_region(settings)
    assert (region.currency, region.ebay_marketplace) == (currency, site)
    with patch.object(marketplaces, "read_settings", return_value=settings):
        assert set(marketplaces.get_selected_marketplaces()) == available
    body = _offer_body("sku", {
        "format": {"format": FIXED_PRICE, "duration": "GTC"},
        "description": "Item", "category_id": "524", "price": "25.00", "quantity": 1,
        "marketplace_id": region.ebay_marketplace, "currency": region.currency,
    }, "warehouse", {})
    assert body["marketplaceId"] == site
    assert body["pricingSummary"]["price"] == {"value": "25.00", "currency": currency}


def test_existing_settings_default_to_us():
    assert seller_region({}).country == "US"
