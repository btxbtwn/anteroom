"""Compare current inventory content with saved marketplace revisions."""
from sqlalchemy.orm import Session

from vendoo_studio.repositories.queries import ListingRepo

# Revisions are never rewritten, so whether two of them hold the same listing
# is settled the first time it is asked. Normalizing both sides costs about a
# millisecond per listing, and the sidebar polls the whole inventory: answered
# fresh every time, it held the GIL for over a second per poll and stalled
# every other request behind it.
_REVISIONS_MATCH: dict[tuple[str, str], bool] = {}
_REVISIONS_MATCH_MAX = 20_000


def unsent_edits(db: Session, conversations: list, revisions: dict[str, str]) -> set[str]:
    """Conversations whose listing moved on after it was last listed.

    A listing live on a marketplace through Anteroom is measured against the
    revisions those listings were written from; Update is where its edits go.
    A differing revision only counts when its content differs too, so a
    revision that only normalized the same listing is not an edit. A listing
    live nowhere has nothing to be ahead of.
    """
    from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo

    ids = [conv.id for conv in conversations]
    direct = MarketplaceListingRepo(db).live_revisions(ids[0] if len(ids) == 1 else None)
    pairs: dict[str, set[tuple[str, str]]] = {}
    for conv in conversations:
        current = revisions.get(conv.id)
        sent = direct.get(conv.id, set())
        differing = {(current, rev) for rev in sent if current and rev != current}
        if differing:
            pairs[conv.id] = differing
    wanted = {pair for group in pairs.values() for pair in group}
    unknown = wanted - _REVISIONS_MATCH.keys()
    if len(_REVISIONS_MATCH) + len(unknown) > _REVISIONS_MATCH_MAX:
        _REVISIONS_MATCH.clear()
        unknown = wanted
    if unknown:
        bodies = ListingRepo(db).revision_bodies({rev for pair in unknown for rev in pair})
        for current, sent in unknown:
            _REVISIONS_MATCH[(current, sent)] = _listings_match(bodies.get(current), bodies.get(sent))
    return {conv_id for conv_id, group in pairs.items() if not all(_REVISIONS_MATCH[pair] for pair in group)}


def _listings_match(a: dict | None, b: dict | None) -> bool:
    """Compare content after normalizing sizes and dropdowns, as revision saves do."""
    import copy
    import hashlib
    import json

    from vendoo_studio.services.listing_generate import align_size_fields
    from vendoo_studio.services.validation import normalize_listing_dropdowns

    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    shaped = []
    for listing in (a, b):
        listing = copy.deepcopy(listing)
        align_size_fields(listing)
        normalize_listing_dropdowns(listing)
        raw = json.dumps(listing, sort_keys=True, default=str, separators=(",", ":"))
        shaped.append(hashlib.sha256(raw.encode()).hexdigest())
    return shaped[0] == shaped[1]


