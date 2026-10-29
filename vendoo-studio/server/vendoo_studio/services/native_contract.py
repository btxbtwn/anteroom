"""The native form, captured for this category, is the generation contract.

Contracts live with the listing revision they describe. They contain only form
definitions, never account/session data. Product evidence stays separate from
the allowed choices: an allowed choice is not evidence for a product fact.
"""
from __future__ import annotations

import copy
import json
import re
from decimal import Decimal, InvalidOperation

from vendoo_studio.services.seller_overrides import get_path, is_locked, locked_paths, marketplace_text, price_for, set_path

KEY = "_native_forms"
EVIDENCE_KEY = "_native_field_evidence"
MARKETS = ("ebay", "poshmark", "mercari", "depop", "sellwild")
SALE_FIELDS = re.compile(
    r"price|ship|parcel|package|quantity|sku|categor|worldwide|offer|format|duration|store|inventor"
    r"|variation|zip|location|condition|title|description|brand|hashtag|tags|charity|donation"
    r"|availab|smartsell|smartpricing|boost|sellernotes"
)
_SCHEMA_KEYS = ("path", "key", "label", "required", "multi", "selection_only", "options", "option_values",
                "max_values", "max_length", "min_value", "max_value", "step", "input_type", "locked",
                "category_specific", "clears", "value_constraints", "help", "inventory_groups", "inventory_system")
_EDITOR_KEYS = ("section", "section_help", "width", "control_width", "presentation", "placeholder", "prefix", "suffix", "counter",
                "label_hidden", "group", "group_help", "reveal", "cascade",
                "option_groups", "option_help", "option_amounts", "slot_labels", "accept", "hashtag_max",
                "save_as_default")


def path_of(row: dict) -> str:
    return str(row.get("path") or row.get("key") or "")


def item_field(row: dict, market: str) -> bool:
    path = path_of(row)
    return (path.startswith(f"{market}_specifics.") and not row.get("locked")
            and not SALE_FIELDS.search(path.split("_specifics.", 1)[-1].casefold())
            and row.get("input_type") not in {"note", "badge", "photos", "inventory", "variations"})


def inputs(listing: dict, market: str, fields: list[dict]) -> dict:
    """Choices that change the fields or options returned by the native builder."""
    paths = {"category_path", "brand", "condition"}
    paths.update(path_of(row) for row in fields if row.get("clears"))
    parents = {rule.get("parentName") for row in fields for rules in (row.get("value_constraints") or {}).values()
               for rule in rules}
    paths.update(path_of(row) for row in fields if row.get("label") in parents)
    if market == "mercari":
        paths.update(("mercari_specifics.package", "mercari_specifics.shippingMethod", "weight_lb", "weight_oz",
                      "package_dimensions_in", "mercari_specifics.smartPricing.enabled"))
    if market == "ebay":
        paths.update(("ebay_specifics.listingFormat", "ebay_specifics.sizeType", "sizeType"))
    if market == "poshmark":
        paths.update(("poshmark_specifics.sizeSetId", "poshmark_specifics.multiItem", "poshmark_specifics.sizeSystem"))
        paths.update(("poshmark_specifics.smartSell.enabled", "poshmark_specifics.availability"))
    if market == "depop":
        paths.add("depop_specifics.worldwideShipping.enabled")
    if market == "sellwild":
        paths.add("sellwild_specifics.nativeCategoryId")
    result = {path: copy.deepcopy(get_path(listing, path)) for path in sorted(paths)}
    # Posting applies marketplace overrides before checking this contract. Compare
    # the same effective choices when capturing the unprepared listing revision.
    result["brand"] = marketplace_text(listing, market, "brand")
    from vendoo_studio.models.schema import ListingSchema

    from vendoo_studio.services.seller_overrides import followed_entry

    condition = followed_entry(listing, market, "condition")
    result["condition"] = condition[1] if condition is not None else ListingSchema.validate_condition(listing.get("condition"))
    result["category_id"] = str(get_path(listing, f"direct_category_ids.{market}")
                                or get_path(listing, f"marketplace_category_ids.{market}") or "")
    return result


def capture(listing: dict, forms: dict[str, dict], markets) -> dict:
    contracts = {}
    for market in markets:
        form = forms.get(market)
        if not form or form.get("known") is False or not form.get("fields"):
            contracts[market] = {"unavailable": True}
            continue
        fields = [{key: copy.deepcopy(row[key]) for key in (*_SCHEMA_KEYS, *_EDITOR_KEYS) if key in row}
                  for row in form.get("fields") or [] if path_of(row)]
        contracts[market] = {"category_id": form.get("category_id"), "fields": fields,
                             "page_labels": bool(form.get("page_labels")),
                             "inputs": inputs(listing, market, fields)}
    return contracts


def editor_form(listing: dict, market: str, *, values: dict | None = None) -> dict | None:
    """Render a saved definition using this revision's values, without a network read.

    A changed controlling choice needs a new definition; never show the old
    category's dropdowns as if they belonged to the new category.
    """
    contract = (listing.get(KEY) or {}).get(market)
    if not isinstance(contract, dict) or contract.get("unavailable") or "page_labels" not in contract:
        return None
    fields = contract.get("fields") or []
    if not fields or inputs(listing, market, fields) != contract.get("inputs"):
        return None
    values = listing if values is None else values
    rows = []
    for definition in fields:
        row = copy.deepcopy(definition)
        path = path_of(row)
        value = get_path(values, path)
        if path == "price":
            value = price_for(values, market)
        name = path.rsplit(".", 1)[-1]
        if value is None and path.startswith(f"{market}_specifics.") and name in {"title", "description", "brand"}:
            value = marketplace_text(values, market, name)
        row["value"] = copy.deepcopy(value) if value is not None else ""
        rows.append(row)
    return {"marketplace": market, "native": True, "known": True,
            "category_id": contract.get("category_id"), "page_labels": contract["page_labels"],
            "fields": rows}


def prompt(contracts: dict) -> str:
    # Presentation metadata belongs to the editor, not the generation context.
    def schema(row, contract):
        definition = {key: value for key, value in row.items() if key in _SCHEMA_KEYS}
        if (path_of(row).startswith("direct_category_ids.") or path_of(row) == "sellwild_specifics.nativeCategoryId") and contract.get("category_id"):
            # Category selection already ran against the complete catalog.
            # Copy generation needs its chosen leaf, not thousands of siblings.
            category_id = str(contract["category_id"])
            values = row.get("option_values") or {}
            options = [label for label in row.get("options") or [] if str(values.get(label, label)) == category_id]
            definition["options"] = options
            definition["option_values"] = {label: values.get(label, label) for label in options}
        if path_of(row).endswith("_specifics.brand") and len(row.get("options") or []) > 300:
            # Brand catalogs contain tens of thousands of unrelated names. Keep
            # the complete editor/validation contract, but only offer the known
            # brand in generation context; a brand choice is never evidence.
            brand = str((contract.get("inputs") or {}).get("brand") or "").strip().casefold()
            options = [label for label in row["options"] if label.casefold() == brand]
            definition["options"] = options
            if "option_values" in row:
                definition["option_values"] = {label: row["option_values"][label] for label in options
                                               if label in row["option_values"]}
        return definition

    contracts = {market: {**contract, "fields": [
        schema(row, contract)
        for row in contract.get("fields") or []]} for market, contract in contracts.items()}
    return (
        "\n\n--- Authoritative native marketplace forms ---\n"
        "For these marketplaces, only the paths below may be generated. These native forms override "
        "generic templates, learned fields and Vendoo fields. Never invent a field or dropdown choice. "
        "Use the exact stored ID for a listed choice. Respect types, lengths and selection limits. "
        "Category is already selected; do not change it. If a form is unavailable, omit its marketplace "
        "specifics and leave the draft incomplete. Use photos and seller evidence for product facts; "
        "a dropdown option is not evidence. Omit unsupported facts, including optional ones. "
        "Do not estimate material, fit or measurements to satisfy a required field. "
        "Title and description still follow the canonical listing rules.\n"
        + json.dumps(contracts, ensure_ascii=False)
    )


def value_error(row: dict, value) -> str | None:
    """Validate a supplied value without truncating, guessing, or selecting a substitute."""
    if value is None or value == "" or value == [] or value == {}:
        return "Required field is empty." if row.get("required") else None
    many = isinstance(value, list)
    if isinstance(value, dict) and row.get("input_type") not in {"inventory", "variations"}:
        return "Use a scalar value."
    if many and not row.get("multi") and row.get("input_type") not in {"inventory", "variations", "photos"}:
        return "Choose one value."
    values = value if many else [value]
    if row.get("max_values") and len(values) > int(row["max_values"]):
        return f"Choose at most {row['max_values']} values."
    if row.get("input_type") == "inventory" and "inventory_groups" in row:
        if not isinstance(value, list):
            return "Use a list of sizes and quantities."
        groups = {str(group["id"]): group for group in row.get("inventory_groups") or []}
        seen = set()
        for item in value:
            if not isinstance(item, dict):
                return "Use a size and quantity for each inventory row."
            group = groups.get(str(item.get("sizeSetId") or ""))
            size_id = str(item.get("sizeId") or "").strip()
            if not group or not size_id:
                return "Choose a size group and size for every inventory row."
            sizes = (group.get("systems") or {}).get(row.get("inventory_system") or "us", group.get("sizes") or [])
            if group["id"] != "custom" and size_id not in {str(size["id"]) for size in sizes}:
                return "Choose a size from this category, group and size system."
            if size_id in seen:
                return "Use each size only once in inventory."
            seen.add(size_id)
            error = value_error({"required": True, "input_type": "number", "min_value": 1, "step": 1,
                                 "max_value": 9007199254740991}, item.get("quantity"))
            if error:
                return "Inventory quantities must be positive whole numbers."
            if len(str(item.get("sku") or "")) > 50:
                return "Inventory SKUs must be at most 50 characters."
    if row.get("selection_only"):
        allowed = {str(item) for item in row.get("options") or []}
        allowed.update(str(item) for item in (row.get("option_values") or {}).values())
        if not allowed or any(str(item) not in allowed for item in values):
            return "Choose a value from this marketplace's current options."
    for item in values:
        if row.get("max_length") and len(str(item)) > int(row["max_length"]):
            return f"Use at most {row['max_length']} characters."
        if row.get("input_type") == "boolean" and not isinstance(item, bool):
            return "Use true or false."
        if row.get("input_type") == "number":
            try:
                number = Decimal(str(item))
            except InvalidOperation:
                return "Use a number."
            if not number.is_finite() or isinstance(item, bool):
                return "Use a finite number."
            if row.get("min_value") is not None and number < Decimal(str(row["min_value"])):
                return f"Use at least {row['min_value']}."
            if row.get("max_value") is not None and number > Decimal(str(row["max_value"])):
                return f"Use at most {row['max_value']}."
            step = Decimal(str(row.get("step") or 0))
            if step > 0 and (number - Decimal(str(row.get("min_value") or 0))) % step:
                return f"Use increments of {row['step']}."
    return None


def applicable_row(row: dict, listing: dict, fields: list[dict]) -> dict:
    """Use the same eBay dependency rules as its native posting validator."""
    constraints = row.get("value_constraints") or {}
    if not constraints:
        return row
    from vendoo_studio.services.marketplace_schemas import aspect_options

    answers = {}
    for parent in fields:
        value = get_path(listing, path_of(parent))
        if value is not None:
            values = value if isinstance(value, list) else [value]
            labels = {str(stored): label for label, stored in (parent.get("option_values") or {}).items()}
            answers[parent.get("label") or path_of(parent).rsplit(".", 1)[-1]] = [labels.get(str(item), str(item)) for item in values]
    choices = aspect_options({"values": [{"label": label, "constraints": constraints.get(label) or []}
                                         for label in row.get("options") or []]}, answers)
    allowed = [choice["label"] for choice in choices]
    return {**row, "options": allowed,
            "option_values": {label: value for label, value in (row.get("option_values") or {}).items() if label in allowed}}


def _leaves(value: dict, prefix: str, allowed: set[str]):
    for key, item in value.items():
        path = f"{prefix}.{key}"
        if isinstance(item, dict) and path not in allowed:
            yield from _leaves(item, path, allowed)
        else:
            yield path, item


def _remove(listing: dict, path: str) -> None:
    parts = path.split(".")
    parent = get_path(listing, ".".join(parts[:-1]))
    if isinstance(parent, dict):
        parent.pop(parts[-1], None)


def constrain_generated(listing: dict, seed: dict, *, freeze_categories: bool = True, keep_evidence: bool = False) -> dict:
    """Discard model-invented paths and invalid answers; preserve server-owned contracts."""
    contracts = seed.get(KEY)
    if not isinstance(contracts, dict):
        listing.pop(KEY, None)
        listing.pop(EVIDENCE_KEY, None)
        return listing
    listing[KEY] = copy.deepcopy(contracts)
    if not keep_evidence:
        listing[EVIDENCE_KEY] = copy.deepcopy(seed.get(EVIDENCE_KEY) or {})
    if seed.get("seller_fields"):
        listing["seller_fields"] = copy.deepcopy(seed["seller_fields"])
    else:
        listing.pop("seller_fields", None)
    # Category selection belongs to the category pass, not the listing writer.
    for key in ("category_path", "direct_category_ids", "marketplace_categories", "marketplace_category_ids",
                "marketplace_category_objects"):
        if freeze_categories and key in seed:
            listing[key] = copy.deepcopy(seed[key])
    for market, contract in contracts.items():
        prefix = f"{market}_specifics"
        block = listing.get(prefix)
        if not isinstance(block, dict):
            continue
        rows = {path_of(row): row for row in contract.get("fields") or []}
        for path, value in list(_leaves(block, prefix, set(rows))):
            if is_locked(seed, path):
                set_path(listing, path, copy.deepcopy(get_path(seed, path)))
            elif path not in rows or value_error({**applicable_row(rows[path], listing, list(rows.values())), "required": False}, value):
                _remove(listing, path)
    for path in locked_paths(seed):
        if path.split(".", 1)[0] in {f"{market}_specifics" for market in contracts}:
            value = get_path(seed, path)
            if value is not None:
                set_path(listing, path, copy.deepcopy(value))
    return listing


def gaps(listing: dict, market: str) -> list[dict]:
    contract = (listing.get(KEY) or {}).get(market)
    if not isinstance(contract, dict):
        return []
    def gap(path, message):
        return {"field": path, "message": message, "blocking": True}
    if contract.get("unavailable"):
        return [gap(f"{market}_specifics", "The marketplace form could not be loaded. Reload its fields before listing.")]
    fields = contract.get("fields") or []
    if inputs(listing, market, fields) != contract.get("inputs"):
        return [gap(f"direct_category_ids.{market}", "The form's category or dependent choices changed. Reload its fields before listing.")]
    found = []
    rows = {path_of(row): row for row in fields}
    for path, value in _leaves(listing.get(f"{market}_specifics") or {}, f"{market}_specifics", set(rows)):
        if path not in rows and value not in (None, "", [], {}):
            found.append(gap(path, "This field is not provided by the selected marketplace form."))
    for path, row in rows.items():
        if row.get("locked") or row.get("input_type") in {"note", "badge", "photos", "inventory", "variations"}:
            continue
        value = get_path(listing, path)
        if path == "price":
            value = price_for(listing, market)
        # Shared prose and price are explicitly offered by the form, but may follow General.
        name = path.rsplit(".", 1)[-1]
        if value is None and name in {"title", "description", "brand"}:
            value = marketplace_text(listing, market, name)
        error = value_error(applicable_row(row, listing, fields), value)
        if error:
            found.append(gap(path, f"{row.get('label') or path}: {error}"))
        elif item_field(row, market) and value not in (None, "", [], {}) and not is_locked(listing, path):
            proof = (listing.get(EVIDENCE_KEY) or {}).get(path) or {}
            if proof.get("value") != value or not proof.get("quote"):
                found.append(gap(path, f"{row.get('label') or path}: Check this answer against photo or seller evidence before listing."))
    return found


def invalidate_dependents(previous: dict, listing: dict) -> None:
    """Clear carried answers when a category or a declared controlling field changes."""
    for market, contract in (previous.get(KEY) or {}).items():
        fields = contract.get("fields") or []
        old_inputs = contract.get("inputs") or {}
        changed = {path for path, value in inputs(listing, market, fields).items() if old_inputs.get(path) != value}
        if not changed:
            continue
        category_changed = any("categor" in path.casefold() for path in changed)
        clear = {path_of(row) for row in fields if category_changed and row.get("category_specific")}
        clear.update(path for row in fields if path_of(row) in changed for path in row.get("clears") or [])
        changed_labels = {row.get("label") for row in fields if path_of(row) in changed}
        clear.update(path_of(row) for row in fields for rules in (row.get("value_constraints") or {}).values()
                     for rule in rules if rule.get("parentName") in changed_labels)
        for path in clear:
            if get_path(previous, path) == get_path(listing, path) and not is_locked(listing, path):
                _remove(listing, path)
