import pytest

from vendoo_studio.services.marketplace_identity import listing_id, listing_reference


@pytest.mark.parametrize(("marketplace", "url", "item_id", "public_url"), [
    ("ebay", "https://ebay.co.uk/itm/Coat/123456789012?tracking=private#details", "123456789012", "https://www.ebay.co.uk/itm/Coat/123456789012"),
    ("etsy", "https://etsy.com/uk/listing/1234567890/coat?ref=tracking", "1234567890", "https://www.etsy.com/uk/listing/1234567890/coat"),
    ("poshmark", "https://www.poshmark.com/listing/Coat-ABCDEF012345678901234567", "abcdef012345678901234567", "https://poshmark.com/listing/Coat-ABCDEF012345678901234567"),
    ("mercari", "https://mercari.com/us/item/m12345678901/", "m12345678901", "https://www.mercari.com/us/item/m12345678901/"),
    ("depop", "https://depop.com/products/seller-coat/?tracking=private", "seller-coat", "https://www.depop.com/products/seller-coat/"),
    ("sellwild", "https://www.sellwild.com/product/123456/coat", "123456", "https://sellwild.com/product/123456/coat"),
    ("vinted", "https://vinted.com/items/1234567890-coat", "1234567890", "https://www.vinted.com/items/1234567890-coat"),
])
def test_shared_identity_strips_tracking_and_accepts_public_host_variants(marketplace, url, item_id, public_url):
    assert listing_reference(marketplace, url) == (item_id, public_url)


@pytest.mark.parametrize("url", [
    "https://ebay.com.evil.example/itm/123456789012",
    "https://evil.example/ebay.com/itm/123456789012",
    "https://ebay.com@evil.example/itm/123456789012",
    "https://user:password@ebay.com/itm/123456789012",
    "https://ebay.com:443/itm/123456789012",
    "https://ebay.com:invalid/itm/123456789012",
    "https://ebay.com/itm/123456789012/../other",
    "https://ebay.com/itm/123456789012evil",
    "https://ebay.com/itm/123456\n789012",
    "https://ebay.com\\@evil.example/itm/123456789012",
    "https://ebay.com/sch/coat", "https://127.0.0.1/itm/123456789012",
    "http://ebay.com/itm/123456789012", "javascript:alert(1)",
])
def test_non_listing_or_forged_urls_never_match_sales(url):
    assert listing_id("ebay", url) is None


def test_wrong_marketplace_and_non_string_are_not_listing_references():
    assert listing_id("etsy", "https://ebay.com/itm/123456789012") is None
    assert listing_id("ebay", {"url": "https://ebay.com/itm/123456789012"}) is None
