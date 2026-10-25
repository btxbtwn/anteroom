"""What a direct List needs before anything is written, per marketplace.

The snapshot a direct List sends is the one Vendoo Send prepares: Item Details
(SKU, package size, original price, category override, labels), condition and
gender normalisation and learned fields, with the marketplace section Vendoo
would save attached as ``form``. Each marketplace's requirements read that
snapshot and answer gaps, ``{"field", "message", "blocking"}``.

A blocking gap refuses List before a job or any write exists, with the result
contract's ``missing_fields``, so the seller (or chat) can fix the listing and
press List again. The rest are shown beside List and do not stop it.

Requirements are plain functions registered with ``requirement``. Each
marketplace module registers its own; ``RULE_MODULES`` names the modules that
hold them, so a marketplace's rules plug in by adding one name.
"""
from __future__ import annotations

import copy
import importlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from vendoo_studio.services.listing_result import failure

NAMES = {"ebay": "eBay", "etsy": "Etsy", "poshmark": "Poshmark", "mercari": "Mercari", "depop": "Depop", "sellwild": "Sellwild", "vinted": "Vinted"}
RULE_MODULES = ("vendoo_studio.services.ebay_listings", "vendoo_studio.services.etsy_listings",
                "vendoo_studio.services.sellwild_listing", "vendoo_studio.services.chrome_listing",
                "vendoo_studio.services.vinted_listing")


@dataclass
class Context:
    """What a requirement may read besides the snapshot."""
    db: Session | None = None
    # Choices made beside List rather than in the listing (Sellwild's category and condition).
    choices: dict[str, Any] = field(default_factory=dict)


Requirement = Callable[[dict, Context], list[dict]]
_REQUIREMENTS: dict[str, list[Requirement]] = {}


def requirement(*marketplaces: str) -> Callable[[Requirement], Requirement]:
    """Register a check that answers the gaps one or more marketplaces would refuse."""
    def register(check: Requirement) -> Requirement:
        for marketplace in marketplaces:
            rules = _REQUIREMENTS.setdefault(marketplace, [])
            if check not in rules:
                rules.append(check)
        return check

    return register


def gap(field_path: str, message: str, *, blocking: bool = True) -> dict:
    return {"field": field_path, "message": message, "blocking": blocking}


def prepare(db: Session, conv, listing_json: dict, marketplace: str) -> dict:
    """The snapshot a direct List sends: Send's preparation plus this marketplace's form."""
    from vendoo_studio.services.job_snapshot import prepare_listing_snapshot
    from vendoo_studio.services.native_form import attach_form

    snapshot = attach_form(prepare_listing_snapshot(db, conv, copy.deepcopy(listing_json)), marketplace)
    if marketplace == "vinted":
        from vendoo_studio.services.vinted_listing import apply_vinted_listing

        apply_vinted_listing(snapshot)
    return snapshot


def _unresolved(snapshot: dict, marketplace: str) -> list[dict]:
    """Values the marketplace section could not encode. Sent empty, so shown, never hidden."""
    form = snapshot.get("form") if isinstance(snapshot.get("form"), dict) else {}
    name = NAMES.get(marketplace, marketplace)
    found = []
    for item in form.get("unresolved") or []:
        label = str(item.get("field") or "")
        prefix, _, aspect = label.partition(":")
        if prefix != marketplace or not aspect:
            continue
        field = f"{marketplace}_specifics.category_specifics.{aspect}"
        value = str(item.get("value") or "").strip()
        if not value:
            # A required field left empty. Refused values are reported first, and their note already says why.
            if not any(existing["field"] == field for existing in found):
                found.append(gap(field, f"{name} needs {aspect}, and it is empty.", blocking=False))
            continue
        found.append(gap(field, f"{aspect} “{value}” is not one of {name}'s choices, so it is left empty.", blocking=False))
    return found


def gaps(snapshot: dict, marketplace: str, context: Context | None = None) -> list[dict]:
    """Every gap this marketplace would refuse or leave empty, most important first."""
    for module in RULE_MODULES:
        importlib.import_module(module)
    context = context or Context()
    found = [*(item for check in _REQUIREMENTS.get(marketplace, []) for item in check(snapshot, context)),
             *_unresolved(snapshot, marketplace)]
    seen: set[tuple[str, str]] = set()
    unique = []
    for item in sorted(found, key=lambda item: not item["blocking"]):
        key = (item["field"], item["message"])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def blocking(found: list[dict]) -> list[dict]:
    return [item for item in found if item["blocking"]]


def refusal(marketplace: str, found: list[dict]) -> dict:
    """The contract result for a List the pre-flight refused. Nothing was sent."""
    needed = blocking(found)
    name = NAMES.get(marketplace, marketplace)
    lead = needed[0]["message"] if len(needed) == 1 else f"{name} needs {len(needed)} more details before listing."
    return failure(lead, "missing_fields", safe=True,
                   fields=[{"field": item["field"], "message": item["message"]} for item in needed])


def seeded_subcategories(db: Session | None, marketplace: str, *, category_id: str = "", path: str = "") -> list[str]:
    """Labels under a stored category, by id or path. Empty for a leaf or a node the tree does not know."""
    if db is None or not (category_id or path):
        return []
    from vendoo_studio.models.catalog import CategoryTreeNode

    query = db.query(CategoryTreeNode).filter_by(marketplace=marketplace)
    node = (query.filter_by(category_id=str(category_id)) if category_id else query.filter_by(path=path)).first()
    if node is None or not node.has_children:
        return []
    children = (db.query(CategoryTreeNode).filter_by(marketplace=marketplace, parent_id=node.category_id)
                .order_by(CategoryTreeNode.category_id).all())
    return [child.label for child in children if child.label]


def seeded_leaf(db: Session | None, marketplace: str, category_id: str) -> bool | None:
    """Whether the stored category tree has this id as a leaf; None when the tree does not know it."""
    if db is None or not category_id:
        return None
    from vendoo_studio.models.catalog import CategoryTreeNode

    node = db.query(CategoryTreeNode).filter_by(marketplace=marketplace, category_id=str(category_id)).one_or_none()
    return None if node is None else bool(node.is_leaf)
