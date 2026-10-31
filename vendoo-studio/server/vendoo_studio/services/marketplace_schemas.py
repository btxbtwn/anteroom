"""eBay's and Etsy's own category schemas, read from their APIs and cached.

eBay: item aspects (``getItemAspectsForCategory``) and allowed conditions
(``getItemConditionPolicies``) per leaf. Etsy: category properties
(``getPropertiesByTaxonomyId``) per taxonomy node, and the seller taxonomy's
current leaves (``getSellerTaxonomyNodes``).

Each category's answer is kept in ``category_field_schemas`` under its own
``marketplace`` key, next to Vendoo's field lists, and read again after
``SCHEMA_MAX_AGE``. Every caller (direct List, the eBay workspace, reviewed eBay
drafts, ``GET /api/marketplace-schemas``) shares the same fetch.

One field shape for both marketplaces::

    {"id", "name", "required", "usage", "mode": "selection" | "free_text",
     "multi", "max_values", "max_length", "values": [{"id", "label", "scale_id"?}]}

eBay names values by their text, so a value's ``id`` is its label there.
"""
from __future__ import annotations

import re
import time
from typing import Literal, TypedDict

from vendoo_studio.services.category_fields import load_rows, save_rows

# v3 keeps aspectEnabledForVariations, so older cached rows are read again.
EBAY_ASPECTS = "ebay_aspects_v3"
EBAY_CONDITIONS = "ebay_conditions"
EBAY_LISTING_TYPES = "ebay_listing_types"
EBAY_LISTING_STRUCTURE = "ebay_listing_structure"
ETSY_PROPERTIES = "etsy_properties"
_TAXONOMY_MAX_AGE_S = 24 * 3600
class _TaxonomyCache(TypedDict):
    at: float
    leaves: tuple[dict[str, str], ...]


_etsy_taxonomy: _TaxonomyCache = {"at": 0.0, "leaves": ()}

SchemaMarketplace = Literal["ebay", "etsy"]


def _category(category_id: str) -> str:
    text = str(category_id or "")
    if not re.fullmatch(r"[1-9]\d{0,14}", text):
        raise ValueError("Choose a category first.")
    return text


def _constraint_rule(rule: dict) -> tuple[str, list[str]] | None:
    """One parent rule, from eBay's names or the names the form already uses.

    Taxonomy returns ``applicableForLocalizedAspectName`` and
    ``applicableForLocalizedAspectValues``. Cached rows and the editor also
    carry ``parentName`` / ``parentValues``. A value such as Size M is valid
    for more than one Size Type, so callers merge those lists.
    """
    if not isinstance(rule, dict):
        return None
    name = str(rule.get("parentName") or rule.get("applicableForLocalizedAspectName") or "").strip()
    raw = rule.get("parentValues")
    if raw is None:
        raw = rule.get("applicableForLocalizedAspectValues") or []
    if not name:
        return None
    values: list[str] = []
    for item in raw or []:
        text = str(item).strip()
        if text and text.casefold() not in {known.casefold() for known in values}:
            values.append(text)
    return name, values


def normalize_value_constraints(rules) -> list[dict]:
    """Parent rules keyed by parent name. Values for the same parent are combined."""
    merged: dict[str, dict] = {}
    order: list[str] = []
    for rule in rules or []:
        parsed = _constraint_rule(rule)
        if not parsed:
            continue
        name, values = parsed
        key = name.casefold()
        if key not in merged:
            merged[key] = {"parentName": name, "parentValues": []}
            order.append(key)
        entry = merged[key]
        for value in values:
            if value.casefold() not in {item.casefold() for item in entry["parentValues"]}:
                entry["parentValues"].append(value)
    return [merged[key] for key in order]


def constraints_by_label(values) -> dict[str, list[dict]]:
    """One constraint list per label. Duplicate labels (two M sizes) share their parents."""
    grouped: dict[str, list] = {}
    for value in values or []:
        rules = value.get("constraints") or []
        label = value.get("label")
        if rules and label:
            grouped.setdefault(label, []).extend(rules)
    return {label: normalize_value_constraints(rules) for label, rules in grouped.items()
            if normalize_value_constraints(rules)}


def _ebay_field(aspect: dict) -> dict:
    constraint = aspect.get("aspectConstraint") or {}
    values = []
    for value in aspect.get("aspectValues") or []:
        if not value.get("localizedValue"):
            continue
        rules = normalize_value_constraints(value.get("valueConstraints") or [])
        values.append({"id": value["localizedValue"], "label": value["localizedValue"],
                       **({"constraints": rules} if rules else {})})
    return {
        "id": aspect["localizedAspectName"], "name": aspect["localizedAspectName"],
        "required": bool(constraint.get("aspectRequired")), "usage": constraint.get("aspectUsage"),
        "mode": "selection" if constraint.get("aspectMode") == "SELECTION_ONLY" else "free_text",
        "multi": constraint.get("itemToAspectCardinality") == "MULTI", "max_values": None,
        "max_length": constraint.get("aspectMaxLength"),
        # eBay lets this aspect tell the variations of one listing apart.
        "variations": bool(constraint.get("aspectEnabledForVariations")),
        "values": values,
    }


def aspect_options(field: dict, answers: dict[str, list[str]]) -> list[dict]:
    """eBay value constraints require a matching parent aspect selection."""
    keyed = {name.casefold(): {str(value).casefold() for value in values} for name, values in answers.items()}
    selected = []
    for value in field.get("values") or []:
        raw = value.get("constraints") or []
        rules = [parsed for parsed in (_constraint_rule(rule) for rule in raw) if parsed]
        if raw and not rules:
            continue
        if all(keyed.get(name.casefold(), set()) & {item.casefold() for item in parents} for name, parents in rules):
            selected.append(value)
    return selected


async def ebay_aspects(client, category_id: str) -> list[dict]:
    """The leaf's item aspects, under eBay's exact names."""
    category_id = _category(category_id)
    cached = load_rows(EBAY_ASPECTS, category_id)
    if cached is not None:
        return cached
    body = await client.request("GET", "/commerce/taxonomy/v1/category_tree/0/get_item_aspects_for_category",
                                params={"category_id": category_id}, app=True)
    fields = [_ebay_field(aspect) for aspect in body.get("aspects") or [] if aspect.get("localizedAspectName")]
    save_rows(EBAY_ASPECTS, category_id, fields)
    return fields


async def ebay_conditions(client, category_id: str) -> dict:
    """``{"required", "conditions": [{"id", "label"}]}`` for the leaf. No conditions means eBay did not say."""
    category_id = _category(category_id)
    cached = load_rows(EBAY_CONDITIONS, category_id)
    if cached:
        return cached[0]
    body = await client.request("GET", "/sell/metadata/v1/marketplace/EBAY_US/get_item_condition_policies",
                                params={"filter": f"categoryIds:{{{category_id}}}"})
    policy = next((row for row in body.get("itemConditionPolicies") or [] if str(row.get("categoryId")) == category_id), {})
    answer = {"required": bool(policy.get("itemConditionRequired")),
              "conditions": [{"id": str(item["conditionId"]), "label": str(item.get("conditionDescription") or "")}
                             for item in policy.get("itemConditions") or [] if item.get("conditionId")]}
    save_rows(EBAY_CONDITIONS, category_id, [answer])
    return answer


def _ebay_listing_type(value: object) -> str | None:
    """eBay's listing type in the Inventory API's spelling."""
    text = re.sub(r"[^A-Z]", "", str(value or "").upper())
    if text in {"AUCTION", "CHINESE"}:
        return "AUCTION"
    if text in {"FIXEDPRICE", "FIXEDPRICEITEM"}:
        return "FIXED_PRICE"
    return None


def ebay_duration(value: object) -> str | None:
    """A listing duration in the Inventory API's spelling: ``GTC`` or ``DAYS_<n>``."""
    text = str(value or "").strip().upper()
    if text == "GTC":
        return "GTC"
    match = re.fullmatch(r"DAYS?_?(\d{1,3})", text)
    return f"DAYS_{int(match.group(1))}" if match and int(match.group(1)) > 0 else None


async def ebay_listing_types(client, category_id: str) -> dict[str, list[str]]:
    """``{"FIXED_PRICE": ["GTC"], "AUCTION": ["DAYS_1", ...]}`` for the leaf.

    A listing type eBay leaves out is one the category does not accept. An
    empty answer means eBay did not say.
    """
    category_id = _category(category_id)
    cached = load_rows(EBAY_LISTING_TYPES, category_id)
    if cached:
        return cached[0]
    body = await client.request("GET", "/sell/metadata/v1/marketplace/EBAY_US/get_listing_type_policies",
                                params={"filter": f"categoryIds:{{{category_id}}}"})
    policy = next((row for row in body.get("listingTypePolicies") or [] if str(row.get("categoryId")) == category_id), {})
    answer: dict[str, list[str]] = {}
    for row in policy.get("listingDurations") or []:
        kind = _ebay_listing_type(row.get("listingType"))
        if not kind:
            continue
        for value in row.get("durationValues") or []:
            duration = ebay_duration(value)
            if duration and duration not in answer.setdefault(kind, []):
                answer[kind].append(duration)
    answer = {kind: values for kind, values in answer.items() if values}
    if answer:
        save_rows(EBAY_LISTING_TYPES, category_id, [answer])
    return answer


async def ebay_variations_supported(client, category_id: str) -> bool | None:
    """Whether eBay takes multi-variation listings in the leaf (``getListingStructurePolicies``). None: eBay did not say."""
    category_id = _category(category_id)
    cached = load_rows(EBAY_LISTING_STRUCTURE, category_id)
    if cached:
        return bool(cached[0].get("variations"))
    body = await client.request("GET", "/sell/metadata/v1/marketplace/EBAY_US/get_listing_structure_policies",
                                params={"filter": f"categoryIds:{{{category_id}}}"})
    policy = next((row for row in body.get("listingStructurePolicies") or [] if str(row.get("categoryId")) == category_id), None)
    if policy is None or "variationsSupported" not in policy:
        return None
    supported = bool(policy["variationsSupported"])
    save_rows(EBAY_LISTING_STRUCTURE, category_id, [{"variations": supported}])
    return supported


def _etsy_field(prop: dict) -> dict:
    values = [{"id": value["value_id"], "label": str(value.get("name") or ""),
               **({"scale_id": value["scale_id"]} if value.get("scale_id") else {})}
              for value in prop.get("possible_values") or [] if value.get("value_id") is not None]
    return {
        "id": prop["property_id"], "name": str(prop.get("display_name") or prop.get("name") or ""),
        "alias": str(prop.get("name") or ""), "required": bool(prop.get("is_required")), "usage": None,
        "mode": "selection" if values else "free_text", "multi": bool(prop.get("is_multivalued")),
        "max_values": prop.get("max_values_allowed"), "max_length": None, "values": values,
    }


async def etsy_properties(taxonomy_id: str) -> list[dict]:
    """The Etsy category's properties (Primary color, Sleeve length…) with their value and scale ids."""
    from vendoo_studio.services import marketplace_accounts

    taxonomy_id = _category(taxonomy_id)
    cached = load_rows(ETSY_PROPERTIES, taxonomy_id)
    if cached is not None:
        return cached
    body = await marketplace_accounts.etsy_api("GET", f"seller-taxonomy/nodes/{taxonomy_id}/properties")
    fields = [_etsy_field(prop) for prop in body.get("results") or []
              if prop.get("property_id") is not None and prop.get("supports_attributes", True)]
    save_rows(ETSY_PROPERTIES, taxonomy_id, fields)
    return fields


async def etsy_taxonomy() -> tuple[dict[str, str], ...]:
    """Etsy's current seller-taxonomy leaves, id and path, read at most once a day."""
    from vendoo_studio.services import marketplace_accounts

    if _etsy_taxonomy["leaves"] and time.monotonic() - float(_etsy_taxonomy["at"]) < _TAXONOMY_MAX_AGE_S:
        return _etsy_taxonomy["leaves"]
    body = await marketplace_accounts.etsy_api("GET", "seller-taxonomy/nodes")
    leaves: list[dict[str, str]] = []

    def walk(nodes, parents: list[str]) -> None:
        for node in nodes or []:
            name = str(node.get("name") or "").strip()
            path = [*parents, name] if name else list(parents)
            children = node.get("children") or []
            if children:
                walk(children, path)
            elif node.get("id") is not None:
                leaves.append({"id": str(node["id"]), "path": " > ".join(path) if path else str(node["id"])})

    walk(body.get("results"), [])
    if leaves:
        _etsy_taxonomy.update(at=time.monotonic(), leaves=tuple(leaves))
    return tuple(leaves)


async def etsy_leaves() -> frozenset[str]:
    """The ids of Etsy's current seller-taxonomy leaves."""
    return frozenset(leaf["id"] for leaf in await etsy_taxonomy())


async def fields(marketplace: SchemaMarketplace, category_id: str) -> list[dict]:
    """Every field the marketplace defines for this category, in the shared shape. eBay's condition comes first."""
    if marketplace == "etsy":
        if _category(category_id) not in await etsy_leaves():
            raise ValueError("Etsy does not list items in that category. Choose a current, most specific Etsy category.")
        return await etsy_properties(category_id)
    from vendoo_studio.services.ebay_client import EbayClient

    client = await EbayClient.connect()
    aspects = await ebay_aspects(client, category_id)
    conditions = await ebay_conditions(client, category_id)
    if not conditions["conditions"]:
        return aspects
    condition = {"id": "condition", "name": "Condition", "required": conditions["required"], "usage": None, "mode": "selection",
                 "multi": False, "max_values": None, "max_length": None,
                 "values": [{"id": item["id"], "label": item["label"]} for item in conditions["conditions"]]}
    return [condition, *aspects]
