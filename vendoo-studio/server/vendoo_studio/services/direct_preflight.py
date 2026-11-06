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

import importlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from vendoo_studio.services.listing_result import failure

NAMES = {"ebay": "eBay", "etsy": "Etsy", "poshmark": "Poshmark", "mercari": "Mercari", "depop": "Depop"}
RULE_MODULES = ("vendoo_studio.services.ebay_listings", "vendoo_studio.services.etsy_listings",
                "vendoo_studio.services.chrome_listing")


@dataclass
class Context:
    """What a requirement may read besides the snapshot."""
    db: Session | None = None
    # Choices made beside List rather than in the listing.
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


def gap(field_path: str, message: str, *, blocking: bool = True, required: bool = False,
        label: str | None = None) -> dict:
    """One answer a marketplace needs. ``required`` marks an empty required field
    that does not refuse List on its own but still counts toward readiness."""
    return {"field": field_path, "message": message, "blocking": blocking,
            **({"required": True} if required else {}), **({"label": label} if label else {})}


_GAP_FIXES = {
    "direct_category_ids.mercari": "anteroom conversations fields --conv-id CONV --path direct_category_ids.mercari --value CATEGORY_ID",
    "mercari_specifics.size": "anteroom conversations fields --conv-id CONV --path mercari_specifics.size --value M",
    "mercari_specifics.package.fitsShoebox": "anteroom conversations fields --conv-id CONV --path mercari_specifics.package.fitsShoebox --value yes",
    "mercari_specifics.package.pounds": "anteroom conversations fields --conv-id CONV --path mercari_specifics.package.pounds --value 1",
    "mercari_specifics.shippingClassId": "anteroom conversations fields --conv-id CONV --path mercari_specifics.shippingClassId --value LABEL_ID",
    "mercari_specifics.shipsFrom": "anteroom conversations fields --conv-id CONV --path mercari_specifics.shipsFrom --value 70125",
    "size": "anteroom conversations fields --conv-id CONV --path size --value M",
    "mercari_specifics.brand": 'anteroom conversations fields --conv-id CONV --path mercari_specifics.brand --value "No Brand/Not sure"',
    "depop_specifics.parcelSize": "anteroom conversations fields --conv-id CONV --path depop_specifics.parcelSize --value Medium",
    "depop_specifics.style": "anteroom conversations fields --conv-id CONV --path depop_specifics.style --clear",
    "package_dimensions_in": "anteroom conversations fields --conv-id CONV --path package_dimensions_in --value 13x10x3",
    "sku": "anteroom conversations fields --conv-id CONV --path sku --value NEW-SKU",
    "etsy_specifics.when_made": 'anteroom conversations fields --conv-id CONV --path etsy_specifics.when_made --value "Before 2007 (Vintage)"',
    **{f"marketplace_photos.{marketplace}": f'anteroom conversations fields --conv-id CONV --path marketplace_photos.{marketplace} --value ["PHOTO_ID"]'
       for marketplace in ("ebay", "etsy", "poshmark", "mercari", "depop")},
    "price": "anteroom set-price --conversation CONV --price 29.99 --all-marketplaces",
}


def with_fix(item: dict) -> dict:
    """Attach the anteroom command that fills this gap, when one is known."""
    if not isinstance(item, dict) or item.get("fix"):
        return item
    command = _GAP_FIXES.get(str(item.get("field") or ""))
    if not command:
        return item
    return {**item, "fix": {"field": item.get("field"), "command": command}}


def prepare(db: Session, conv, listing_json: dict, marketplace: str) -> dict:
    """The snapshot a direct List sends: Send's preparation plus this marketplace's form."""
    from vendoo_studio.services.job_snapshot import prepare_listing_snapshot
    from vendoo_studio.services.native_form import attach_form

    snapshot = prepare_listing_snapshot(db, conv, listing_json)
    from vendoo_studio.services.seller_overrides import apply_marketplace_overrides

    apply_marketplace_overrides(snapshot, marketplace)
    snapshot = attach_form(snapshot, marketplace)
    return snapshot


def _unresolved(snapshot: dict, marketplace: str) -> list[dict]:
    """Values the marketplace section could not encode. Sent empty, so shown, never hidden."""
    form = snapshot.get("form")
    if not isinstance(form, dict):
        form = {}
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
                found.append(gap(field, f"{name} needs {aspect}, and it is empty.", blocking=False,
                                 required=True, label=aspect))
            continue
        if aspect == "condition":
            # The row shows the grade it would send; say that, not that the row is unfilled.
            found.append(gap(field, f"“{value}” could not be matched to one of {name}'s conditions, so this listing "
                                   "would send none. Pick the condition above.", blocking=False))
            continue
        found.append(gap(field, f"{aspect} “{value}” is not one of {name}'s choices, so it is left empty.", blocking=False))
    return found


# The sell forms Anteroom builds locally, without a network read, before the
# marketplace's own form has been captured for this listing.
_LOCAL_FORMS = {"poshmark": "vendoo_studio.services.poshmark_form",
                "depop": "vendoo_studio.services.depop_form",
                "mercari": "vendoo_studio.services.mercari_form"}
_NOT_ANSWERS = {"note", "badge", "photos", "variations"}


def _empty(row: dict) -> bool:
    value = row.get("value")
    if row.get("input_type") == "inventory":
        # Poshmark's Size: required only where the category has sizes, answered by any picked size.
        return bool(row.get("inventory_groups")) and not any(
            isinstance(item, dict) and item.get("sizeId") for item in value or [])
    return value in (None, "", [], {})


def _empty_form_rows(snapshot: dict, marketplace: str) -> list[dict]:
    """Required rows of the marketplace's own form the listing leaves empty.

    A captured form contract is checked by ``native_contract.gaps``; before one
    exists the locally built form names the same required rows. They are said
    beside List and counted as missing; the marketplace refuses them on List.
    """
    from vendoo_studio.services import native_contract

    if (snapshot.get(native_contract.KEY) or {}).get(marketplace) or marketplace not in _LOCAL_FORMS:
        return []
    form = importlib.import_module(_LOCAL_FORMS[marketplace]).form(snapshot)
    found = []
    for row in form.get("fields") or []:
        path = str(row.get("path") or row.get("key") or "")
        if (not path or not row.get("required") or row.get("locked") or row.get("input_type") in _NOT_ANSWERS
                or native_contract.account_resolved(row)):
            continue
        if _empty(row) and not _general_answer(snapshot, path):
            label = str(row.get("label") or "").strip() or None
            found.append(gap(path, f"{label or path}: Required field is empty.", blocking=False,
                             required=True, label=label))
    return found


def _general_answer(snapshot: dict, path: str) -> bool:
    """Whether List fills an empty marketplace row from General or the prepared form.

    A Poshmark or Depop condition, brand or color left empty on its own form is
    sent from the listing's General answer of the same name.
    """
    name = path.rsplit(".", 1)[-1]
    form = snapshot.get("form")
    form = form if isinstance(form, dict) else {}
    overrides = form.get("overrides")
    overrides = overrides if isinstance(overrides, dict) else {}
    return any(value not in (None, "", [], {}) for value in (snapshot.get(name), overrides.get(name)))


def gaps(snapshot: dict, marketplace: str, context: Context | None = None) -> list[dict]:
    """Every gap this marketplace would refuse or leave empty, most important first."""
    for module in RULE_MODULES:
        importlib.import_module(module)
    context = context or Context()
    found = [*(item for check in _REQUIREMENTS.get(marketplace, []) for item in check(snapshot, context)),
             *_unresolved(snapshot, marketplace)]
    from vendoo_studio.services.native_contract import gaps as native_form_gaps

    found.extend(native_form_gaps(snapshot, marketplace))
    found.extend(_empty_form_rows(snapshot, marketplace))
    # One missing answer is said once. The module checks and the captured form
    # both name the same field, and the form's own row is the less informative
    # way to say it, so a field keeps its first message after the blocking ones
    # and the specific ones are preferred over "Required field is empty."
    # The form's own label names the field however it is said.
    seen: dict[str, dict] = {}
    for item in sorted(found, key=lambda item: (not item["blocking"], _generic(item["message"]))):
        kept = seen.setdefault(same_answer(item["field"]), item)
        if kept is not item and item.get("label") and not kept.get("label"):
            seen[same_answer(item["field"])] = {**kept, "label": item["label"]}
    return list(seen.values())


# A marketplace form's copy of a shared field follows General until the seller
# edits it on that form, so both paths name one answer.
_FOLLOWED = frozenset({"title", "description", "brand", "department"})


def same_answer(field: str) -> str:
    """The key two reports of one missing answer share."""
    head, _, leaf = str(field).rpartition(".")
    if leaf in _FOLLOWED and head.endswith("_specifics"):
        return leaf
    return str(field)


def _generic(message: str) -> bool:
    return "Required field is empty" in str(message)


def blocking(found: list[dict]) -> list[dict]:
    return [item for item in found if item["blocking"]]


def refusal(marketplace: str, found: list[dict]) -> dict:
    """The contract result for a List the pre-flight refused. Nothing was sent."""
    needed = blocking(found)
    name = NAMES.get(marketplace, marketplace)
    lead = needed[0]["message"] if len(needed) == 1 else f"{name} needs {len(needed)} more details before listing."
    return failure(lead, "missing_fields", safe=True,
                   fields=[{"field": item["field"], "message": item["message"]} for item in needed])


def seeded_leaf(db: Session | None, marketplace: str, category_id: str) -> bool | None:
    """Whether the stored category tree has this id as a leaf; None when the tree does not know it."""
    if db is None or not category_id:
        return None
    from vendoo_studio.models.catalog import CategoryTreeNode

    node = db.query(CategoryTreeNode).filter_by(marketplace=marketplace, category_id=str(category_id)).one_or_none()
    return None if node is None else bool(node.is_leaf)
