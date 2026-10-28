"""Public listing references shared by direct actions and sales reconciliation.

A valid URL identifies a listing; it does not verify ownership or live status.
No network requests are made here.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

_HOSTS = {
    "ebay": {"ebay.com", "ebay.co.uk", "ebay.ca", "ebay.com.au", "ebay.de", "ebay.fr", "ebay.it", "ebay.es"},
    "etsy": {"etsy.com"},
    "poshmark": {"poshmark.com"},
    "mercari": {"mercari.com"},
    "depop": {"depop.com"},
    "sellwild": {"sellwild.com"},
    "vinted": {"vinted.com"},
    "facebook": {"facebook.com", "m.facebook.com", "web.facebook.com"},
}
_PATHS = {
    "ebay": r"/itm/(?:[^/]+/)?(?P<id>\d{9,15})",
    "etsy": r"/(?:[a-z-]+/)?listing/(?P<id>[1-9]\d*)(?:/[^/]+)?",
    "poshmark": r"/(?:my-)?listing/(?:[^/]*-)?(?P<id>[a-fA-F0-9]{24})",
    "mercari": r"/(?:us/)?item/(?P<id>m\d{6,})",
    # The seller's own product page ends in /manage, which is what the address bar shows.
    "depop": r"/products/(?P<id>[a-z0-9][a-z0-9-]{0,200})(?:/manage)?",
    "sellwild": r"/product/(?P<id>\d+)(?:/[^/]+)?",
    "vinted": r"/items/(?P<id>\d+)(?:-[^/]+)?",
    "facebook": r"/marketplace/item/(?P<id>\d{6,20})",
}


def listing_reference(marketplace: str, url: str | None) -> tuple[str, str] | None:
    """Return the listing ID and a public URL with tracking information removed."""
    if not isinstance(url, str) or marketplace not in _HOSTS:
        return None
    clean = url.strip()
    if not clean or len(clean) > 2048 or "\\" in clean or any(c.isspace() or ord(c) < 32 for c in clean):
        return None
    try:
        parsed = urlsplit(clean)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port is not None
                or host not in _HOSTS[marketplace]):
            return None
    except ValueError:
        return None
    match = re.fullmatch(_PATHS[marketplace], parsed.path.rstrip("/"))
    if not match:
        return None
    item_id = match["id"].lower() if marketplace == "poshmark" else match["id"]
    if marketplace in {"facebook", "depop"}:
        # Depop's manage view is the seller's own page; the public one is the product alone.
        return item_id, public_listing_url(marketplace, item_id)
    public_host = host if marketplace in {"poshmark", "sellwild"} else f"www.{host}"
    return item_id, urlunsplit(("https", public_host, parsed.path, "", ""))


def listing_id(marketplace: str, url: str | None) -> str | None:
    reference = listing_reference(marketplace, url)
    return reference[0] if reference else None


_PUBLIC = {
    "ebay": "https://www.ebay.com/itm/{}",
    "etsy": "https://www.etsy.com/listing/{}",
    "poshmark": "https://poshmark.com/listing/{}",
    "mercari": "https://www.mercari.com/us/item/{}/",
    "depop": "https://www.depop.com/products/{}/",
    "sellwild": "https://sellwild.com/product/{}",
    "vinted": "https://www.vinted.com/items/{}",
    "facebook": "https://www.facebook.com/marketplace/item/{}/",
}


def public_listing_url(marketplace: str, item_id: str) -> str:
    """The public link for a listing ID, in the form listing_reference reads back."""
    return _PUBLIC[marketplace].format(item_id)
