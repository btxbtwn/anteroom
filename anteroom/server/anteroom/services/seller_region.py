"""Marketplace coverage and currency for the seller's home region."""

from dataclasses import dataclass
from typing import Literal

from anteroom.services.user_settings import read_settings, update_settings

RegionCode = Literal["US", "CA", "GB", "AU"]


@dataclass(frozen=True)
class SellerRegion:
    country: RegionCode
    currency: str
    ebay_marketplace: str
    poshmark_domain: str | None
    marketplaces: frozenset[str]


_COMMON = frozenset({"ebay", "etsy", "depop"})
REGIONS: dict[str, SellerRegion] = {
    "US": SellerRegion("US", "USD", "EBAY_US", "poshmark.com", _COMMON | {"poshmark", "mercari"}),
    "CA": SellerRegion("CA", "CAD", "EBAY_CA", "poshmark.ca", _COMMON | {"poshmark", "mercari"}),
    "GB": SellerRegion("GB", "GBP", "EBAY_GB", None, _COMMON),
    "AU": SellerRegion("AU", "AUD", "EBAY_AU", None, _COMMON),
}


def seller_region(settings: dict | None = None) -> SellerRegion:
    payload = read_settings() if settings is None else settings
    return REGIONS.get(str(payload.get("seller_region")), REGIONS["US"])


def set_seller_region(country: RegionCode) -> SellerRegion:
    update_settings(lambda settings: settings.__setitem__("seller_region", country))
    return REGIONS[country]
