"""The photos each marketplace's form sends, like Vendoo's Photo Manager.

A form sends the item's photos in item order until the seller changes that
marketplace's own pick, ``marketplace_photos.<marketplace>`` on the listing:
the ticked photos in the seller's order, which may include photos added on
that form only (``Photo.marketplace``). Reset removes the pick. Each
marketplace keeps at most its own number of photos; the rest are not sent.
"""
from __future__ import annotations

from vendoo_studio.services.listing_videos import VIDEO_MARKETPLACES, VIDEO_RULES

# The most photos each marketplace's own listing form takes.
PHOTO_LIMITS = {"ebay": 24, "etsy": 20, "poshmark": 16, "mercari": 12, "depop": 8, "vinted": 20, "sellwild": 20}


def rules() -> dict:
    return {"photo_limits": PHOTO_LIMITS, "video_rules": VIDEO_RULES}


def picked_ids(listing: dict | None, marketplace: str) -> list[str]:
    """The seller's own pick for this marketplace, in order. Empty when the form uses the item's photos."""
    picks = listing.get("marketplace_photos") if isinstance(listing, dict) else None
    chosen = picks.get(marketplace) if isinstance(picks, dict) else None
    if not isinstance(chosen, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in chosen if str(item).strip()))


def chosen_photos(listing: dict | None, photos: list, marketplace: str) -> list:
    """Every photo this marketplace's form has ticked, before its limit. ``photos`` is the item's photos and every form's own."""
    item = [photo for photo in photos if photo.marketplace is None]
    ids = picked_ids(listing, marketplace)
    if not ids:
        return item
    usable = {photo.id: photo for photo in photos if photo.marketplace in (None, marketplace)}
    # A pick whose photos were all deleted goes back to the item's photos rather than sending none.
    return [usable[photo_id] for photo_id in ids if photo_id in usable] or item


def over_limit(marketplace: str, count: int) -> str:
    from vendoo_studio.services.native_marketplaces import NAMES

    limit = PHOTO_LIMITS[marketplace]
    if count <= limit:
        return ""
    name = NAMES[marketplace]
    return f"{name} keeps {limit} photos. The first {limit} of {count} are sent. Untick photos on the {name} form to choose which."


def photos_for(listing: dict | None, photos: list, marketplace: str) -> tuple[list, str]:
    """The photos this marketplace is sent, in order, and a warning when some are left off."""
    chosen = chosen_photos(listing, photos, marketplace)
    return chosen[:PHOTO_LIMITS[marketplace]], over_limit(marketplace, len(chosen))


def keep_media(previous: dict | None, listing: dict) -> None:
    """A regenerated listing keeps the seller's photo picks and videos from the one it replaces."""
    if not isinstance(previous, dict):
        return
    if isinstance(previous.get("marketplace_photos"), dict) and previous["marketplace_photos"]:
        listing["marketplace_photos"] = dict(previous["marketplace_photos"])
    for marketplace in VIDEO_MARKETPLACES:
        bucket = f"{marketplace}_specifics"
        before = previous.get(bucket)
        if not isinstance(before, dict):
            before = {}
        if "videoId" not in before:
            continue
        after = listing.get(bucket)
        if not isinstance(after, dict):
            after = {}
        listing[bucket] = {**after, "videoId": before["videoId"]}
