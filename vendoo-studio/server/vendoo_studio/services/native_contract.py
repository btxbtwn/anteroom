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
from typing import Any

from vendoo_studio.services.seller_overrides import get_path, is_locked, locked_paths, marketplace_text, price_for, set_path

KEY = "_native_forms"
EVIDENCE_KEY = "_native_field_evidence"
MARKETS = ("ebay", "poshmark", "mercari", "depop", "sellwild")
SALE_FIELDS = re.compile(
    r"price|ship|parcel|package|quantity|sku|categor|worldwide|offer|format|duration|store|inventor"
    r"|variation|zip|location|condition|title|description|brand|hashtag|tags|charity|donation"
    r"|availab|smartsell|smartpricing|boost|sellernotes|polic"
)
_SCHEMA_KEYS = ("path", "key", "label", "required", "multi", "selection_only", "options", "option_values",
                "max_values", "max_length", "min_value", "max_value", "step", "input_type", "locked",
                "category_specific", "clears", "value_constraints", "help", "inventory_groups", "inventory_system",
                "variation_aspects")
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


def _category_path(path, listing: dict):
    """The category path posting prepares: the shared path mapped to its selectable leaf."""
    if not isinstance(path, str) or not path.strip():
        return path
    try:
        from vendoo_studio.services.registry import map_vendoo_category_path

        return map_vendoo_category_path(path, listing)
    except Exception:  # noqa: BLE001 - an unmapped path is still the choice it names
        return path


def _input_paths(market: str, fields: list[dict]) -> set[str]:
    """Paths whose value can change the fields or options the native builder returns."""
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
    return paths


def _raw_inputs(listing: dict, market: str, fields: list[dict]) -> dict:
    """The choices as the listing itself stores them, before posting preparation."""
    result = {path: copy.deepcopy(get_path(listing, path)) for path in sorted(_input_paths(market, fields))}
    result["category_id"] = str(get_path(listing, f"direct_category_ids.{market}")
                                or get_path(listing, f"marketplace_category_ids.{market}") or "")
    # Posting maps the shared path to the leaf its category picker names. Compare
    # the mapped path, so a path recorded before preparation is not a change.
    result["category_path"] = _category_path(result.get("category_path"), listing)
    return result


def inputs(listing: dict, market: str, fields: list[dict]) -> dict:
    """Choices that change the fields or options returned by the native builder."""
    result = _raw_inputs(listing, market, fields)
    # Posting applies marketplace overrides before checking this contract. Compare
    # the same effective choices when capturing the unprepared listing revision.
    result["brand"] = marketplace_text(listing, market, "brand")
    from vendoo_studio.models.schema import ListingSchema

    from vendoo_studio.services.seller_overrides import followed_entry

    condition = followed_entry(listing, market, "condition")
    result["condition"] = condition[1] if condition is not None else ListingSchema.validate_condition(listing.get("condition"))
    return result


def _condition_inputs(value: dict, market: str, fields: list[dict]) -> dict:
    """Compare eBay's condition label and its native id as the same choice."""
    result = copy.deepcopy(value)
    if market == "ebay":
        condition = str(result.get("condition") or "").strip().casefold()
        for row in fields:
            if path_of(row).casefold() == "ebay_specifics.condition":
                for label, native_id in (row.get("option_values") or {}).items():
                    if str(label).strip().casefold() == condition:
                        result["condition"] = str(native_id)
    return result


def _answered(value):
    """One spelling for "no answer", so the same choice compares equal either way.

    A controlling choice is stored as ``""``, ``None``, ``[]`` or ``{}``
    depending on which writer last touched it, and an unset boolean reaches the
    listing as ``False``. None of those is a choice, so a form saved with one
    spelling is not stale against the same no-answer in another.
    """
    if value is None or value is False or value == "" or value == [] or value == {}:
        return None
    return value


def _same_answers(left: dict, right: dict) -> bool:
    """Whether two input maps hold the same choices, comparing over their union.

    A path one side never stored and the other stored empty is one no-answer.
    """
    return all(_answered(left.get(path)) == _answered(right.get(path))
               for path in set(left) | set(right))


def _stored_inputs(listing: dict, stored) -> dict | None:
    """A saved contract's inputs in the current terms, or None when it has none."""
    if not isinstance(stored, dict):
        return None
    canonical = copy.deepcopy(stored)
    if "category_path" in canonical:
        canonical["category_path"] = _category_path(canonical.get("category_path"), listing)
    return canonical


def inputs_match(listing: dict, market: str, fields: list[dict], stored) -> bool:
    """True when ``stored`` still names this listing's current controlling choices.

    A contract recorded before posting compared marketplace overrides stores
    the General brand and condition, and one recorded before category paths
    were mapped stores the raw shared path. Compare those raw choices too, so
    upgrading does not make an unchanged saved form look stale.
    """
    saved = _stored_inputs(listing, stored)
    if saved is None:
        return False
    saved = _condition_inputs(saved, market, fields)
    return any(_same_answers(_condition_inputs(candidate, market, fields), saved)
               for candidate in (inputs(listing, market, fields), _raw_inputs(listing, market, fields)))


def changed_inputs(listing: dict, market: str, fields: list[dict], stored) -> set[str]:
    """Controlling choices that differ from the saved contract's, in current terms."""
    current = _condition_inputs(inputs(listing, market, fields), market, fields)
    saved = _condition_inputs(_stored_inputs(listing, stored) or {}, market, fields)
    if _same_answers(current, saved) or _same_answers(_condition_inputs(_raw_inputs(listing, market, fields), market, fields), saved):
        return set()
    return {path for path in set(current) | set(saved)
            if _answered(current.get(path)) != _answered(saved.get(path))}


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
    if not fields or not inputs_match(listing, market, fields, contract.get("inputs")):
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
    rows = {path_of(item): item for item in fields}
    for parent in fields:
        path = path_of(parent)
        # The parent is answered under whichever spelling this listing uses,
        # and by General when this form leaves the row empty.
        value = _first_answer(_row_values(listing, _market_of(path, rows), path, rows))
        if value in (None, "", [], {}):
            value = shared_answer(listing, parent, _market_of(path, rows), path)
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


def _fold(path: str) -> str:
    """One key for two spellings of the same path.

    The form names its rows the way the marketplace's page does ("Size Type",
    "Brand") while the listing JSON stores aspects under their own keys
    ("sizeType", "brand"). Both spellings reach the same control, so a value
    written under either one answers that row.
    """
    return re.sub(r"[^a-z0-9]", "", str(path or "").casefold())


def _market_of(path: str, rows: dict[str, dict]) -> str:
    """The marketplace a form path belongs to."""
    head = str(path).split(".", 1)[0]
    if head.endswith("_specifics"):
        return head[: -len("_specifics")]
    for candidate in ("ebay", "poshmark", "mercari", "depop", "sellwild", "vinted", "etsy"):
        if any(str(row).startswith(f"{candidate}_specifics.") for row in rows):
            return candidate
    return ""


def _row_values(listing: dict, market: str, path: str, rows: dict[str, dict]) -> list:
    """Every value this listing stores for ``path``, whatever its spelling.

    Only spellings the form does not itself name are folded in: a row keeps the
    value stored at its own path first, and a differently-spelled key is read
    only when that row would otherwise look empty.
    """
    found = [get_path(listing, path)]
    want = _fold(path)
    prefix = f"{market}_specifics."
    block = listing.get(f"{market}_specifics")
    if not isinstance(block, dict):
        return found
    for stored, value in _leaves(block, prefix, set(rows)):
        if stored in rows or _fold(stored) != want:
            continue
        found.append(value)
    return found


def _first_answer(values: list) -> Any:
    """The first spelling that carries an answer, so an empty one does not hide it."""
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return values[0] if values else None


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
    def gap(path, message, *, blocking=True):
        return {"field": path, "message": message, "blocking": blocking}
    if contract.get("unavailable"):
        return [gap(f"{market}_specifics", "The marketplace form could not be loaded. Reload its fields before listing.")]
    fields = contract.get("fields") or []
    if not inputs_match(listing, market, fields, contract.get("inputs")):
        return [gap(f"direct_category_ids.{market}", "The form's category or dependent choices changed. Reload its fields before listing.")]
    found = []
    rows = {path_of(row): row for row in fields}
    folded = {_fold(path) for path in rows}
    for path, value in _leaves(listing.get(f"{market}_specifics") or {}, f"{market}_specifics", set(rows)):
        if path in rows or _fold(path) in folded or value in (None, "", [], {}):
            continue
        # A value this form no longer has a row for is left out of the send; it
        # does not stop the listing. The seller reads it, and can clear it.
        found.append(gap(path, "This field is not provided by the selected marketplace form.", blocking=False))
    for path, row in rows.items():
        if row.get("locked") or row.get("input_type") in {"note", "badge", "photos", "inventory", "variations"}:
            continue
        if not required_for_this_listing(row, listing, path):
            continue
        value = get_path(listing, path)
        # A value read from General or Settings is not the model's claim, so it
        # needs no photo evidence of its own.
        borrowed = False
        if path == "price":
            value = price_for(listing, market)
        # Shared prose and price are explicitly offered by the form, but may follow General.
        name = path.rsplit(".", 1)[-1]
        if value is None and name in {"title", "description", "brand"}:
            value = marketplace_text(listing, market, name)
            borrowed = True
        if value in (None, "", [], {}):
            value = _first_answer(_row_values(listing, market, path, rows))
            # An answer stored under another spelling of this path was written
            # before this form knew the row; it is not a new model claim.
            borrowed = value not in (None, "", [], {})
        if value in (None, "", [], {}):
            value = shared_answer(listing, row, market, path)
            borrowed = True
        if value in (None, "", [], {}):
            value = settings_answer(listing, path)
            borrowed = True
        if account_resolved(row) and value in (None, "", [], {}):
            # List picks this from the seller's account, so the row is not
            # empty in any way that stops the listing.
            found.append(gap(path, f"{row.get('label') or path}: Uses the account's own choice.", blocking=False))
            continue
        error = value_error(applicable_row(row, listing, fields), value)
        if error:
            # A policy or location with no stored answer is chosen from the
            # account at List. Missing one here does not stop the listing, so it
            # is said, not refused.
            found.append(gap(path, f"{row.get('label') or path}: {error}",
                             blocking=not account_resolved(row)))
        elif (item_field(row, market) and not borrowed and value not in (None, "", [], {})
              and not is_locked(listing, path)):
            proof = _evidence(listing, path)
            if proof.get("value") != value or not proof.get("quote"):
                # A required answer with no proof would be sent on the model's
                # word alone, so it holds the listing. An optional one is only
                # noted: dropping it changes nothing the marketplace demands.
                found.append(gap(path, f"{row.get('label') or path}: Check this answer against photo or seller evidence before listing.",
                                 blocking=bool(row.get("required"))))
    return found


def account_resolved(row: dict) -> bool:
    """A row List fills from the seller's marketplace account rather than the listing."""
    return path_of(row).rsplit(".", 1)[-1] in {"fulfillmentPolicyId", "paymentPolicyId", "returnPolicyId",
                                               "merchantLocationKey"}


def required_for_this_listing(row: dict, listing: dict, path: str) -> bool:
    """Whether this row's requirement applies to the listing as it is set up.

    eBay's start bid is required only for an auction. The form marks it required
    unconditionally, which would block every Buy It Now listing.
    """
    if not row.get("required") or path != "ebay_specifics.auctionStartPrice":
        return True
    from vendoo_studio.services.marketplace_schemas import _ebay_listing_type

    ebay = listing.get("ebay_specifics") if isinstance(listing.get("ebay_specifics"), dict) else {}
    return _ebay_listing_type(ebay.get("listingFormat")) == "AUCTION"


# eBay policies and the item location are chosen in Settings, not on the form.
_SETTINGS_CHOICES = {"ebay_specifics.fulfillmentPolicyId": "fulfillment_policy_id",
                     "ebay_specifics.paymentPolicyId": "payment_policy_id",
                     "ebay_specifics.returnPolicyId": "return_policy_id",
                     "ebay_specifics.merchantLocationKey": "merchant_location_key"}


def settings_answer(listing: dict, path: str) -> str | None:
    """The value Settings holds for a row the form leaves to the seller's account.

    List already falls back to these defaults; pre-flight reads the same choice
    so a policy set once in Settings is not reported as an empty row.
    """
    setting = _SETTINGS_CHOICES.get(path)
    if not setting:
        return None
    from vendoo_studio.services.user_settings import get_ebay_listing_defaults

    return str(get_ebay_listing_defaults().get(setting) or "").strip() or None


def _evidence(listing: dict, path: str) -> dict:
    """The proof recorded for this row, under whichever spelling it was written."""
    recorded = listing.get(EVIDENCE_KEY) or {}
    proof = recorded.get(path)
    if isinstance(proof, dict):
        return proof
    want = _fold(path)
    for stored, value in recorded.items():
        if _fold(stored) == want and isinstance(value, dict):
            return value
    return {}


def shared_answer(listing: dict, row: dict, market: str, path: str) -> Any:
    """The General answer a marketplace takes for a row the listing leaves empty.

    eBay's posting path already fills these aspects from the listing's own
    fields (``ebay_listings._GENERAL_ASPECTS``). Pre-flight reads the same
    answers, so a row that will be sent filled is not reported as empty. A
    marketplace override on this form still wins, through ``marketplace_text``.
    """
    name = path.rsplit(".", 1)[-1]
    if market == "ebay":
        # Only the aspects posting itself fills from the listing's own fields.
        # A policy, price or category is never answered by a General value.
        from vendoo_studio.services.ebay_listings import _GENERAL_ASPECTS, _aspect_key

        if _aspect_key(name) == "condition":
            # ``ebay_condition`` reads the form's own grade and falls back to
            # General, so the row is answered whenever General names one.
            said = str(listing.get("condition") or "").strip()
            if said:
                for label, native_id in (row.get("option_values") or {}).items():
                    if str(label).strip().casefold() == said.casefold():
                        return str(native_id)
                return said

        for aspect, keys in _GENERAL_ASPECTS.items():
            if _aspect_key(aspect) != _aspect_key(name):
                continue
            for key in keys:
                value = listing.get(key)
                if value not in (None, "", [], {}):
                    return value
    # No other marketplace fills its item-specific rows from General at send:
    # their forms keep the seller's own answer, so an empty row stays empty.
    return None


def invalidate_dependents(previous: dict, listing: dict) -> None:
    """Clear carried answers when a category or a declared controlling field changes."""
    for market, contract in (previous.get(KEY) or {}).items():
        fields = contract.get("fields") or []
        # Invalidate for this edit, even if the saved form is still stale from
        # an earlier edit. Form freshness is checked separately by gaps().
        changed = changed_inputs(listing, market, fields, inputs(previous, market, fields))
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
