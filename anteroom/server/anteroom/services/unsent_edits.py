"""Compare current inventory content with saved marketplace revisions."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import threading

from sqlalchemy.orm import Session

from anteroom.repositories.queries import ListingRepo

# Most revisions are immutable; the one in-place rewrite (chrome_catalog
# direct-category resolution) invalidates this cache after commit and bumps
# _REVISIONS_GENERATION so in-flight readers never store a pre-commit match.
_REVISIONS_LOCK = threading.Lock()
_REVISIONS_MATCH: dict[tuple[str, str], bool] = {}
_REVISIONS_GENERATION = 0
_REVISIONS_MATCH_MAX = 20_000


def invalidate_revision_matches(revision_id: str) -> None:
    """Drop cached match pairs for a revision whose JSON was rewritten in place."""
    global _REVISIONS_GENERATION
    with _REVISIONS_LOCK:
        _REVISIONS_GENERATION += 1
        stale = [pair for pair in _REVISIONS_MATCH if revision_id in pair]
        for pair in stale:
            _REVISIONS_MATCH.pop(pair, None)


def _canonicalize_numbers(value):
    """Canonicalize whole-number floats so 42 and 42.0 hash identically, matching Python ==."""
    if isinstance(value, dict):
        return {k: _canonicalize_numbers(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_canonicalize_numbers(v) for v in value]
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return value


def normalized_listing_hash(listing: dict | None) -> str | None:
    """SHA-256 of ``listing`` after normalizing sizes and dropdowns, as revision saves do."""
    if not isinstance(listing, dict):
        return None
    from anteroom.services.listing_generate import align_size_fields
    from anteroom.services.validation import normalize_listing_dropdowns

    shaped = copy.deepcopy(listing)
    align_size_fields(shaped)
    normalize_listing_dropdowns(shaped)
    canonical = _canonicalize_numbers(shaped)
    raw = json.dumps(canonical, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def unsent_edits(
    db: Session,
    conversations: list,
    revisions: dict[str, str],
    *,
    commit_backfill: bool = False,
) -> set[str]:
    """Conversations whose listing moved on after it was last listed.

    A listing live on a marketplace through Anteroom is measured against the
    revisions those listings were written from; Update is where its edits go.
    A differing revision only counts when its content differs too, so a
    revision that only normalized the same listing is not an edit. A listing
    live nowhere has nothing to be ahead of.
    """
    from anteroom.repositories.marketplace_listings import MarketplaceListingRepo

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
    with _REVISIONS_LOCK:
        generation = _REVISIONS_GENERATION
        results = {pair: _REVISIONS_MATCH[pair] for pair in wanted if pair in _REVISIONS_MATCH}
        unknown = wanted - results.keys()
        if len(_REVISIONS_MATCH) + len(unknown) > _REVISIONS_MATCH_MAX:
            _REVISIONS_MATCH.clear()
            results.clear()
            unknown = wanted
    if unknown:
        needed = {rev for pair in unknown for rev in pair}
        repo = ListingRepo(db)
        hashes = repo.revision_hashes(needed)
        missing = {rev for rev in needed if hashes.get(rev) is None}
        if missing:
            bodies = repo.revision_bodies(missing)
            computed: dict[str, str] = {}
            for rev_id, body in bodies.items():
                digest = normalized_listing_hash(body)
                if digest is not None:
                    hashes[rev_id] = digest
                    computed[rev_id] = digest
            if (
                commit_backfill
                and computed
                and not (db.new or db.dirty or db.deleted or db.info.get("changed_topics"))
            ):
                was_quiet = db.info.get("quiet_changes")
                db.info["quiet_changes"] = True
                try:
                    repo.store_revision_hashes(computed)
                    db.commit()
                finally:
                    if was_quiet is None:
                        db.info.pop("quiet_changes", None)
                    else:
                        db.info["quiet_changes"] = was_quiet
        resolved: dict[tuple[str, str], bool] = {}
        for current, sent in unknown:
            left = hashes.get(current)
            match = left is not None and left == hashes.get(sent)
            resolved[(current, sent)] = match
            results[(current, sent)] = match
        with _REVISIONS_LOCK:
            # Skip caching if an in-place revision rewrite committed while our
            # DB read was in flight so pre-commit hashes cannot poison the cache.
            if _REVISIONS_GENERATION == generation:
                if len(_REVISIONS_MATCH) + len(resolved) > _REVISIONS_MATCH_MAX:
                    _REVISIONS_MATCH.clear()
                _REVISIONS_MATCH.update(resolved)
    return {conv_id for conv_id, group in pairs.items() if not all(results[pair] for pair in group)}
