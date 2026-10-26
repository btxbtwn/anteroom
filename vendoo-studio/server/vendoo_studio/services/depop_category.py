"""One Depop category on a listing.

Depop's leaf is stored in several places: ``direct_category_ids.depop`` (what
the extension reads first), ``depop_specifics.categoryId``,
``marketplace_category_ids.depop``, and ``marketplace_category_objects.depop``
with its breadcrumb in ``marketplace_categories.depop``. Changing only some of
them left the rest naming the old leaf, so a category move sent Depop the old
product type and its size set. The leaf the seller chose is written to all of
them, and a size id picked for the old leaf is dropped.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

from vendoo_studio.services.vendoo_categories import category_from_hit, path_parts

_ID = re.compile(r"^(kidswear|menswear|womenswear|everything-else)_(.+)$")
# Where each id lives, in the order the extension reads them.
_ID_KEYS = (("direct_category_ids", "depop"), ("depop_specifics", "categoryId"), ("marketplace_category_ids", "depop"))
_LOCKS = ("direct_category_ids.depop", "depop_specifics.categoryId", "marketplace_category_ids.depop")
_PATH_LOCK = "marketplace_categories.depop"


def _bucket(listing: dict, name: str) -> dict:
    value = listing.get(name) if isinstance(listing, dict) else None
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _ids(listing: dict) -> list[str]:
    """The stored ids, then the category object's id."""
    found = [_text(_bucket(listing, bucket).get(key)) for bucket, key in _ID_KEYS]
    found.append(_text(_bucket(_bucket(listing, "marketplace_category_objects"), "depop").get("id")))
    return found


def _breadcrumb(listing: dict) -> str:
    return " > ".join(path_parts(_bucket(listing, "marketplace_categories").get("depop")))


def depop_leaf(category_id: str = "", path: str = "") -> dict[str, Any] | None:
    """Depop's leaf from the seeded category tree, by id or by breadcrumb."""
    from vendoo_studio.config import DATABASE_PATH
    from vendoo_studio.services.vendoo_create import tree_leaf

    wanted = " > ".join(path_parts(path))
    if category_id:
        try:
            with sqlite3.connect(DATABASE_PATH) as db:
                row = db.execute(
                    "SELECT path FROM category_tree_nodes WHERE marketplace = 'depop' AND category_id = ? AND is_leaf = 1",
                    (category_id,),
                ).fetchone()
        except sqlite3.Error:
            row = None
        if not row:
            return None
        wanted = str(row[0])
    if not wanted:
        return None
    leaf = category_from_hit(tree_leaf("depop", wanted), wanted)
    return leaf if leaf and leaf.get("id") and (not category_id or leaf["id"] == category_id) else None


def apply_depop_category(listing: dict, category_id: str) -> bool:
    """Point every Depop category key at ``category_id``. A stale category object is dropped."""
    if not isinstance(listing, dict) or not _ID.match(category_id or ""):
        return False
    before = repr([_ids(listing), _breadcrumb(listing), _bucket(listing, "depop_specifics").get("category_path"),
                   _bucket(listing, "marketplace_category_objects").get("depop")])
    for bucket, key in _ID_KEYS:
        values = dict(_bucket(listing, bucket))
        values[key] = category_id
        listing[bucket] = values
    objects = dict(_bucket(listing, "marketplace_category_objects"))
    leaf = depop_leaf(category_id)
    if leaf:
        objects["depop"] = leaf
        parts = path_parts(leaf.get("displayPath"))
        listing["marketplace_categories"] = {**_bucket(listing, "marketplace_categories"), "depop": " > ".join(parts)}
        depop = listing["depop_specifics"]
        if "category_path" in depop:
            depop["category_path"] = parts
    else:
        # The object's extras name the old leaf's size set. Without a tree row, none is better.
        objects.pop("depop", None)
    listing["marketplace_category_objects"] = objects
    after = repr([_ids(listing), _breadcrumb(listing), _bucket(listing, "depop_specifics").get("category_path"),
                  _bucket(listing, "marketplace_category_objects").get("depop")])
    return before != after


def _changed_category(previous: dict, listing: dict) -> str:
    """The Depop leaf this save changed to, if it changed one."""
    for new, old in zip(_ids(listing), _ids(previous), strict=True):
        if new and new != old and _ID.match(new):
            return new
    path = _breadcrumb(listing)
    if path and path != _breadcrumb(previous):
        leaf = depop_leaf(path=path)
        if leaf:
            return str(leaf["id"])
    return ""


def _locked_category(listing: dict) -> str:
    """The one Depop leaf the seller set by hand, when the locked keys agree."""
    from vendoo_studio.services.seller_overrides import get_path, locked_paths

    locks = locked_paths(listing)
    chosen = {_text(get_path(listing, path)) for path in _LOCKS if path in locks}
    if _PATH_LOCK in locks:
        leaf = depop_leaf(path=_breadcrumb(listing))
        chosen.add(str(leaf["id"]) if leaf else "")
    chosen.discard("")
    found = next(iter(chosen)) if len(chosen) == 1 else ""
    return found if _ID.match(found) else ""


def align_depop_category(listing: dict, previous: dict | None = None) -> bool:
    """Keep the Depop category keys on one leaf.

    On a save, the key the seller changed wins. On a job snapshot (no
    ``previous``), the key the seller locked wins over an older resolved id.
    """
    if not isinstance(listing, dict):
        return False
    old_category = next((value for value in _ids(previous or {}) if value), "")
    chosen = _changed_category(previous, listing) if isinstance(previous, dict) else _locked_category(listing)
    if not chosen or all(value == chosen for value in _ids(listing)):
        return False
    changed = apply_depop_category(listing, chosen)
    depop = listing.get("depop_specifics")
    if (isinstance(previous, dict) and old_category and chosen != old_category and isinstance(depop, dict)
            and "nativeSizeId" in depop and depop.get("nativeSizeId") == _bucket(previous, "depop_specifics").get("nativeSizeId")):
        # A size id from the old leaf's size set. Depop's size comes from the new set.
        depop.pop("nativeSizeId")
        changed = True
    return changed
