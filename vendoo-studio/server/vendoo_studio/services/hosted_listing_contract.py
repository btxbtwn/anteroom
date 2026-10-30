"""Editable listing values only; operational identities belong to server records."""
from fastapi import HTTPException
from pydantic import ValidationError

from vendoo_studio.services.hosted_fields import DraftFields

MARKETS = frozenset({'ebay', 'poshmark', 'mercari', 'depop', 'etsy', 'sellwild', 'vinted', 'grailed', 'facebook'})
ROOTS = frozenset(DraftFields.model_fields) | {'title', 'originalPrice', 'tags', 'material', 'style', 'care', 'flaws', 'measurements', 'direct_category_ids', 'marketplace_photos'} | {f'{market}_specifics' for market in MARKETS}
BLOCKED = frozenset({'userid', 'conversationid', 'revisionid', 'parentrevisionid', 'apikey', 'token', 'accesstoken', 'refreshtoken',
                     'authorization', 'cookie', 'cookies', 'password', 'headers', 'postid', 'listingid', 'draftid', 'itemid',
                     'storedfilename', 'sourcefilename', 'filename', 'filepath', 'url', 'listingurl', 'drafturl', 'vendooitemid'})


def editable_listing(raw):
    if not isinstance(raw, dict) or any(key not in ROOTS for key in raw):
        raise HTTPException(422, 'Only listing fields can be saved')
    categories = raw.get('direct_category_ids')
    if categories is not None and (not isinstance(categories, dict) or any(
        key not in MARKETS and key.removesuffix('_path') not in MARKETS for key in categories)):
        raise HTTPException(422, 'Only marketplace category identities can be supplied')
    try:
        DraftFields.model_validate({key: value for key, value in raw.items() if key in DraftFields.model_fields})
    except ValidationError:
        raise HTTPException(422, 'Enter valid General listing values') from None
    if raw.get('title') is not None and (not isinstance(raw['title'], str) or len(raw['title']) > 200):
        raise HTTPException(422, 'Enter a title of up to 200 characters')
    nodes = 0

    def validate(value, depth=0):
        nonlocal nodes
        nodes += 1
        if depth > 8 or nodes > 10000:
            raise HTTPException(422, 'Listing fields exceed the size limit')
        if value is None or isinstance(value, bool):
            return
        if isinstance(value, str):
            if len(value) > 20000:
                raise HTTPException(422, 'A listing field is too long')
            return
        if isinstance(value, int | float):
            import math
            if not math.isfinite(value):
                raise HTTPException(422, 'Enter a finite number')
            return
        if isinstance(value, list):
            for item in value:
                validate(item, depth + 1)
            return
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = ''.join(character for character in key.lower() if character.isalnum())
                if key.startswith('_') or len(key) > 100 or normalized in BLOCKED:
                    raise HTTPException(422, 'Operational fields cannot be supplied')
                validate(item, depth + 1)
            return
        raise HTTPException(422, 'Unsupported listing value')

    validate(raw)
    return raw


def validate_media(db, conversation_id, listing):
    """Media selectors name saved assets on this item, never another row's IDs."""
    from sqlalchemy import select
    from vendoo_studio.models.conversation import Photo
    from vendoo_studio.services.marketplace_media import PHOTO_LIMITS

    picks = listing.get('marketplace_photos')
    if picks is not None:
        if not isinstance(picks, dict) or any(market not in PHOTO_LIMITS for market in picks):
            raise HTTPException(422, 'Choose photos for a supported marketplace')
        for market, identifiers in picks.items():
            if (not isinstance(identifiers, list) or len(identifiers) > 100
                    or any(not isinstance(value, str) for value in identifiers)
                    or len(set(identifiers)) != len(identifiers)):
                raise HTTPException(422, 'Choose each photo once, in the order to send it')
            valid = set(db.scalars(select(Photo.id).where(Photo.conversation_id == conversation_id,
                Photo.id.in_(identifiers), (Photo.marketplace.is_(None) | (Photo.marketplace == market)))))
            if valid != set(identifiers):
                raise HTTPException(422, 'Choose photos from this listing and marketplace form')
    for market in MARKETS:
        values = listing.get(f'{market}_specifics')
        if values is not None and not isinstance(values, dict):
            raise HTTPException(422, 'Marketplace fields must be an object')
        if (values or {}).get('videoId'):
            raise HTTPException(422, 'Listing videos are available in the desktop app')


def remove_photo_selections(db, conversation_id, photo_id):
    """Keep the current form picks usable after an explicitly deleted photo."""
    import copy
    from sqlalchemy import select
    from vendoo_studio.models.listing import Listing, ListingRevision

    listing = db.get(Listing, conversation_id)
    if listing is None or not listing.current_revision_id:
        return
    revision = db.scalar(select(ListingRevision).where(ListingRevision.id == listing.current_revision_id,
                                                       ListingRevision.conversation_id == conversation_id))
    if revision is None or not isinstance(revision.listing_json, dict):
        return
    payload = copy.deepcopy(revision.listing_json)
    picks = payload.get('marketplace_photos')
    if not isinstance(picks, dict):
        return
    changed = False
    for market, identifiers in list(picks.items()):
        if isinstance(identifiers, list) and photo_id in identifiers:
            picks[market] = [value for value in identifiers if value != photo_id]
            changed = True
    if changed:
        updated = ListingRevision(conversation_id=conversation_id, listing_json=payload,
                                  source='photo_removed', parent_revision_id=revision.id)
        db.add(updated)
        db.flush()
        listing.current_revision_id = updated.id
        listing.validation_status = None
        listing.validation_errors = None
