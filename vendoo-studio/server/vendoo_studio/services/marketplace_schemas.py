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
from typing import Literal

from vendoo_studio.services.category_fields import load_rows, save_rows

EBAY_ASPECTS = "ebay_aspects"
EBAY_CONDITIONS = "ebay_conditions"
ETSY_PROPERTIES = "etsy_properties"
_TAXONOMY_MAX_AGE_S = 24 * 3600
_etsy_taxonomy: dict[str, object] = {"at": 0.0, "leaves": ()}

SchemaMarketplace = Literal["ebay", "etsy"]


def _category(category_id: str) -> str:
    text = str(category_id or "")
    if not re.fullmatch(r"[1-9]\d{0,14}", text):
        raise ValueError("Choose a category first.")
    return text


def _ebay_field(aspect: dict) -> dict:
    constraint = aspect.get("aspectConstraint") or {}
    return {
        "id": aspect["localizedAspectName"], "name": aspect["localizedAspectName"],
        "required": bool(constraint.get("aspectRequired")), "usage": constraint.get("aspectUsage"),
        "mode": "selection" if constraint.get("aspectMode") == "SELECTION_ONLY" else "free_text",
        "multi": constraint.get("itemToAspectCardinality") == "MULTI", "max_values": None,
        "max_length": constraint.get("aspectMaxLength"),
        "values": [{"id": value["localizedValue"], "label": value["localizedValue"]}
                   for value in aspect.get("aspectValues") or [] if value.get("localizedValue")],
    }


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
        return _etsy_taxonomy["leaves"]  # type: ignore[return-value]
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
