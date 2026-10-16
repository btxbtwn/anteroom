"""Vendoo's ``categoryV2`` objects, and picking one from its search answers.

Breadcrumbs, category search hits and Vendoo's category mapping all reduce to
the ``{id, displayPath, …}`` object the form stores when a tree leaf is chosen.
"""

from __future__ import annotations

import json
from typing import Any

from vendoo_studio.services.vendoo_item_shape import _clean, _norm


def path_parts(path: Any) -> list[str]:
    """Split a Vendoo breadcrumb (string or list) into displayPath parts."""
    if isinstance(path, list):
        return [str(part).strip() for part in path if str(part or "").strip()]
    text = str(path or "").strip()
    if not text:
        return []
    return [part.strip() for part in text.split(">") if part.strip()]


def category_v2(category_id: Any = None, path: Any = None) -> dict[str, Any] | None:
    """Build the ``categoryV2`` object Vendoo's form stores when a tree leaf is chosen."""
    parts = path_parts(path)
    cat_id = _clean(category_id)
    if not cat_id and not parts:
        return None
    out: dict[str, Any] = {}
    if cat_id:
        out["id"] = cat_id
    if parts:
        out["displayPath"] = parts
    return out or None


def pick_category_hit(matches: list[Any] | None, wanted_path: str) -> dict[str, Any] | None:
    """Choose the search hit that best matches the Studio breadcrumb."""
    wanted_parts = path_parts(wanted_path)
    wanted_norm = _norm(" > ".join(wanted_parts))
    pool = [m for m in (matches or []) if isinstance(m, dict)]
    if not pool:
        return None

    def hit_parts(hit: dict[str, Any]) -> list[str]:
        return hit_display_path(hit)

    leaves = [m for m in pool if m.get("is_leaf") is True]
    candidates = leaves or pool
    for hit in candidates:
        parts = hit_parts(hit)
        if parts and _norm(" > ".join(parts)) == wanted_norm:
            return hit
    if wanted_parts:
        leaf = _norm(wanted_parts[-1])
        for hit in candidates:
            parts = hit_parts(hit)
            if parts and _norm(parts[-1]) == leaf:
                return hit
    return candidates[0]


def hit_display_path(hit: dict[str, Any]) -> list[str]:
    """Breadcrumb parts from a search hit, whichever key carries them."""
    for key in ("all_category_label", "displayPath", "display_path", "searchable_text", "path", "text"):
        parts = path_parts(hit.get(key))
        if parts:
            return parts
    return []


def _hit_extras(hit: dict[str, Any]) -> dict[str, Any] | None:
    """``extras`` is indexed as a JSON string (``payload_text``)."""
    extras = hit.get("extras")
    if isinstance(extras, dict):
        return extras
    raw = hit.get("payload_text")
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


def _path_words(parts: list[str]) -> set[str]:
    """Every distinct word in these path segments, normalised."""
    return {word for part in parts for word in _norm(part).split()}


# Parent-category words that every Tops/Shirts child repeats in its leaf label.
# Counting them as a leaf match made "Tank Tops" beat "Tees - Short Sleeve"
# whenever the general leaf was bare "Tops".
_PARENT_LEAF_WORDS = frozenset({"tops", "top", "shirts", "shirt", "clothing", "clothes"})
# Specialty subtypes Vendoo often returns for a plain tee/blouse under Tops.
_SUBTYPE_LEAF_WORDS = frozenset({
    "tank", "tanks", "crop", "crops", "halter", "tube", "muscle", "cami", "camisole",
    "tunic", "tunics",
})
_TEE_LEAF_WORDS = frozenset({"tee", "tees", "tshirt", "tshirts"})


def pick_mapped_category(
    general: dict[str, Any],
    match: dict[str, Any] | None,
    recommendations: list[dict[str, Any]] | None,
    want_path: str = "",
) -> dict[str, Any] | None:
    """The best of Vendoo's mapping answers for a general category.

    Its ``match`` is a single guess and is sometimes the wrong branch — a
    girls' t-shirt came back as Boys' on eBay and as Tanks on Etsy, with the
    right leaf sitting in ``recommendations``. Score them all against the
    general category the seller chose: agreeing with its leaf counts most,
    then agreeing anywhere in the path. The match keeps ties, so this only
    overrides it when something genuinely fits better.

    ``want_path`` is this marketplace's own breadcrumb when the listing named
    one. The general category is a coarser thing than a Poshmark leaf — it
    says "Tops", not "Blouses" — so scoring against it alone throws the
    breadcrumb away and lets the mapper settle on whichever subtype it likes.
    """
    candidates = [c for c in [match, *(recommendations or [])] if isinstance(c, dict) and c.get("id")]
    if not candidates:
        return None
    want_parts = path_parts(want_path) or [str(part) for part in (general.get("displayPath") or [])]
    if not want_parts:
        return candidates[0]
    want_all = _path_words(want_parts)
    want_leaf = _path_words(want_parts[-1:])
    # Bare "Tops" / "Shirts" leaves have no distinctive garment word — prefer a
    # tee recommendation over Tank Tops when Vendoo's match is the subtype.
    bare_parent_leaf = bool(want_leaf) and want_leaf <= _PARENT_LEAF_WORDS
    want_names_tank = bool(want_all & _SUBTYPE_LEAF_WORDS)

    def score(candidate: dict[str, Any]) -> tuple[int, int, int]:
        parts = [str(part) for part in hit_display_path(candidate)]
        if not parts:
            return (0, 0, 0)
        leaf_words = _path_words(parts[-1:])
        # Only distinctive leaf words count — "tops" inside "Tank Tops" is an
        # echo of the parent category, not a real leaf match.
        leaf_hits = len((want_leaf - _PARENT_LEAF_WORDS) & leaf_words)
        leaf_hits += len(want_leaf & (leaf_words - _PARENT_LEAF_WORDS))
        path_hits = len(want_all & _path_words(parts))
        garment = 0
        if leaf_words & _TEE_LEAF_WORDS:
            garment = 2
        elif leaf_words & _SUBTYPE_LEAF_WORDS:
            garment = 0 if (bare_parent_leaf and not want_names_tank) else 1
        else:
            garment = 1
        return (leaf_hits, path_hits, garment)

    best = max(range(len(candidates)), key=lambda i: (*score(candidates[i]), -i))
    return candidates[best]


def category_from_hit(hit: dict[str, Any] | None, fallback_path: str = "") -> dict[str, Any] | None:
    """Turn a ``/api/category/search`` hit into Vendoo's full ``categoryV2``.

    The picker stores more than ``{id, displayPath}``: the eBay form needs the
    ancestor id chain and ``extras.siteId`` to look the leaf's aspects up, and
    throws without them. Anything the hit does not carry is left off rather
    than invented, so a thin hit still yields the minimal object.
    """
    if not isinstance(hit, dict):
        return category_v2(path=fallback_path)
    cat_id = hit.get("id") or hit.get("category_id") or hit.get("value") or hit.get("key")
    out = category_v2(cat_id, hit_display_path(hit) or fallback_path)
    if not out:
        return None
    label = hit.get("last_subcategory_label") or hit.get("most_relevant_text") or hit.get("displayName")
    if isinstance(label, str) and label.strip():
        out["displayName"] = label.strip()
    for source, dest in (("is_leaf", "isLeaf"), ("isLeaf", "isLeaf"),
                         ("has_children", "hasChildren"), ("hasChildren", "hasChildren")):
        if dest not in out and hit.get(source) is not None:
            out[dest] = bool(hit[source])
    ancestors = hit.get("parent_category_id_path")
    if isinstance(ancestors, list) and cat_id:
        chain = [str(part) for part in ancestors if str(part or "").strip()]
        out["path"] = [*chain, str(cat_id)]
    extras = _hit_extras(hit)
    if extras is not None:
        out["extras"] = extras
    return out


def _resolved_category(listing: dict[str, Any], marketplace: str) -> dict[str, Any] | None:
    """The full ``categoryV2`` the resolver stashed for this marketplace."""
    objects = listing.get("marketplace_category_objects")
    hit = objects.get(marketplace) if isinstance(objects, dict) else None
    return dict(hit) if isinstance(hit, dict) and hit.get("id") else None


def _category(listing: dict[str, Any]) -> Any:
    """Prefer a resolved Vendoo category object; fall back to the display path."""
    resolved = _resolved_category(listing, "general")
    if resolved:
        return resolved
    resolved = category_v2(listing.get("category_id"), listing.get("category_path"))
    if resolved and resolved.get("id"):
        return resolved
    path = _clean(listing.get("category_path"))
    return path
