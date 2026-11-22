"""Marketplace coverage and currency for the seller's home region."""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

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


def seller_region(settings: dict | None = None, *, db: Session | None = None) -> SellerRegion:
    if settings is None:
        from anteroom.services.hosted_context import HostedSettingsStore
        from anteroom.services.service_context import current

        context = current()
        if context is None:
            settings = read_settings()
        else:
            store = getattr(context, "settings", None)
            if store is None:
                store = HostedSettingsStore(context.session_factory, owner=context.seller_id)
            settings = store.load(db) if db is not None else store.read()
    return REGIONS.get(str((settings or {}).get("seller_region")), REGIONS["US"])


def set_seller_region(country: RegionCode) -> SellerRegion:
    update_settings(lambda settings: settings.__setitem__("seller_region", country))
    return REGIONS[country]
