"""Editable listing values only; operational identities belong to server records."""
from fastapi import HTTPException
from pydantic import ValidationError

from vendoo_studio.services.hosted_fields import DraftFields

MARKETS = frozenset({'ebay', 'poshmark', 'mercari', 'depop', 'etsy', 'sellwild', 'vinted', 'grailed', 'facebook'})
ROOTS = frozenset(DraftFields.model_fields) | {'title', 'originalPrice', 'tags', 'material', 'style', 'care', 'flaws', 'measurements', 'direct_category_ids'} | {f'{market}_specifics' for market in MARKETS}
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
