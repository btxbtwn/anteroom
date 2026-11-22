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

from anteroom.services.seller_overrides import CANONICAL_PRICE, get_path, is_locked, locked_paths, marketplace_text, price_for, set_path

KEY = "_native_forms"
EVIDENCE_KEY = "_native_field_evidence"
MARKETS = ("ebay", "poshmark", "mercari", "depop", "etsy")
SALE_FIELDS = re.compile(
    r"price|ship|parcel|package|quantity|sku|categor|worldwide|offer|format|duration|store|inventor"
    r"|variation|zip|location|condition|title|description|brand|hashtag|tags|charity|donation"
    r"|availab|smartsell|smartpricing|boost|sellernotes|polic"
    r"|whomade|whatisit|whenmade|renewaloption"
)
_SCHEMA_KEYS = ("path", "key", "label", "required", "multi", "selection_only", "options", "option_values",
                "choice_list", "max_values", "max_length", "min_value", "max_value", "step", "input_type", "locked",
                "category_specific", "clears", "value_constraints", "help", "inventory_groups", "inventory_system",
                "variation_aspects")
_EDITOR_KEYS = ("section", "section_help", "width", "control_width", "presentation", "placeholder", "prefix", "suffix", "counter",
                "label_hidden", "group", "group_help", "reveal", "cascade",
                "option_groups", "option_help", "option_amounts", "slot_labels", "accept", "hashtag_max",
                "save_as_default")


def copy_listing(listing: dict) -> dict:
    """A deep copy of a listing that shares its saved forms instead of copying them.

    Saved forms can be megabytes, and every reader only reads them: changing a
    form means capturing a new one and assigning it to the listing. Copying them
    for each form and marketplace kept the hosted server's one CPU busy.
    """
    copied = copy.deepcopy({key: value for key, value in listing.items() if key != KEY})
    if KEY in listing:
        copied[KEY] = listing[KEY]
    return copied


def path_of(row: dict) -> str:
    return str(row.get("path") or row.get("key") or "")


def is_sale_field(path: str) -> bool:
    """Whether a marketplace specifics path holds listing/account/sale config rather than item attributes."""
    if path.startswith("etsy_specifics.category_specifics."):
        return False
    return bool(SALE_FIELDS.search(path.split("_specifics.", 1)[-1].casefold()))


def item_field(row: dict, market: str) -> bool:
    path = path_of(row)
    return (path.startswith(f"{market}_specifics.") and not row.get("locked")
            and not is_sale_field(path)
            and row.get("input_type") not in {"note", "badge", "photos", "inventory", "variations"})


def _category_path(path, listing: dict):
    """The category path posting prepares: the shared path mapped to its selectable leaf."""
    if not isinstance(path, str) or not path.strip():
        return path
    try:
        from anteroom.services.registry import map_vendoo_category_path

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
        paths.discard("brand")
        paths.discard("mercari_specifics.brand")
        paths.discard("mercari_specifics.noBrand")
        paths.update(("mercari_specifics.package", "mercari_specifics.shippingMethod", "weight_lb", "weight_oz",
                      "package_dimensions_in", "mercari_specifics.smartPricing.enabled"))
    if market == "ebay":
        paths.update(("ebay_specifics.listingFormat", "ebay_specifics.sizeType", "sizeType"))
    if market == "poshmark":
        paths.update(("poshmark_specifics.sizeSetId", "poshmark_specifics.multiItem", "poshmark_specifics.sizeSystem"))
        paths.update(("poshmark_specifics.smartSell.enabled", "poshmark_specifics.availability"))
    if market == "depop":
        paths.add("depop_specifics.worldwideShipping.enabled")
    return paths


def _raw_inputs(listing: dict, market: str, fields: list[dict]) -> dict:
    """The choices as the listing itself stores them, before posting preparation."""
    result = {path: copy.deepcopy(get_path(listing, path)) for path in sorted(_input_paths(market, fields))}
    if market == "ebay":
        # Normalization writes sizeType while the native schema names Size
        # Type. Both control the same dropdown; spelling changes are not edits.
        aspects = listing.get("ebay_specifics") or {}
        for path, value in result.items():
            if not path.startswith("ebay_specifics.") or value not in (None, "", [], {}):
                continue
            name = path.split(".", 1)[1]
            result[path] = copy.deepcopy(next((answer for key, answer in aspects.items()
                                               if _fold(key) == _fold(name) and answer not in (None, "", [], {})), value))
    result["category_id"] = str(get_path(listing, f"direct_category_ids.{market}") or "")
    # Posting maps the shared path to the leaf its category picker names. Compare
    # the mapped path, so a path recorded before preparation is not a change.
    result["category_path"] = _category_path(result.get("category_path"), listing)
    return result


def inputs(listing: dict, market: str, fields: list[dict]) -> dict:
    """Choices that change the fields or options returned by the native builder."""
    result = _raw_inputs(listing, market, fields)
    # Posting applies marketplace overrides before checking this contract. Compare
    # the same effective choices when capturing the unprepared listing revision.
    if "brand" in result:
        result["brand"] = marketplace_text(listing, market, "brand")
    from anteroom.models.schema import ListingSchema

    from anteroom.services.seller_overrides import followed_entry

    condition = followed_entry(listing, market, "condition")
    result["condition"] = condition[1] if condition is not None else ListingSchema.validate_condition(listing.get("condition"))
    if market == "mercari":
        # Preflight and List answer an unanswered shoebox question from the
        # seller's default (attach_form). Capture reads the saved listing
        # without it, so compare the answer both will send.
        from anteroom.services.user_settings import apply_mercari_shoebox_default

        effective = {"mercari_specifics": {"package": copy.deepcopy(result.get("mercari_specifics.package"))}}
        if apply_mercari_shoebox_default(effective):
            result["mercari_specifics.package"] = effective["mercari_specifics"]["package"]
    return result


def _zero_empty_number(value: Any, *, keep_zero: bool = False) -> Any:
    """Treat unset weights/dimensions as one no-answer; keep zero as an answer when keep_zero is set."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        return value
    if not number.is_finite() or (number == 0 and not keep_zero):
        return None
    return number.normalize()


def _normalize_mercari_package(pkg: Any) -> Any:
    if not isinstance(pkg, dict):
        return pkg
    normalized: dict[str, Any] = {}
    for key in ("pounds", "ounces"):
        value = pkg.get(key)
        # package() treats boolean native weights as numeric 0/1 answers.
        clean = _zero_empty_number(int(value) if isinstance(value, bool) else value, keep_zero=True)
        if clean is not None:
            normalized[key] = clean
    # mercari_form.package() uses native pounds/ounces once either is answered
    # (including explicit 0), defaulting an unset companion to 0.
    if normalized:
        normalized.setdefault("pounds", Decimal(0))
        normalized.setdefault("ounces", Decimal(0))
    for key in ("length", "width", "height"):
        clean = _zero_empty_number(pkg.get(key), keep_zero=True)
        if clean is not None:
            normalized[key] = clean
    fits = str(pkg.get("fitsShoebox") or "").strip().casefold()
    if fits:
        normalized["fitsShoebox"] = fits
    if pkg.get("skipDimensions") in (True, "true"):
        normalized["skipDimensions"] = True
    return normalized or None


def _condition_inputs(value: dict, market: str, fields: list[dict]) -> dict:
    """Compare equivalent marketplace input spellings (eBay condition IDs, Mercari zero weights)."""
    result = copy.deepcopy(value)
    if market == "ebay":
        condition = str(result.get("condition") or "").strip().casefold()
        for row in fields:
            if path_of(row).casefold() == "ebay_specifics.condition":
                for label, native_id in (row.get("option_values") or {}).items():
                    if str(label).strip().casefold() == condition:
                        result["condition"] = str(native_id)
    if market == "mercari":
        for key in ("brand", "mercari_specifics.brand", "mercari_specifics.noBrand"):
            result.pop(key, None)
        for key in ("weight_lb", "weight_oz"):
            if key in result:
                result[key] = _zero_empty_number(result[key])
        if "mercari_specifics.package" in result:
            result["mercari_specifics.package"] = _normalize_mercari_package(result["mercari_specifics.package"])
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
    from anteroom.services import choice_lists

    contracts = {}
    for market in markets:
        form = forms.get(market)
        if not form or form.get("known") is False or form.get("properties_unavailable") or not form.get("fields"):
            contracts[market] = {"unavailable": True}
            continue
        compacted = choice_lists.compact_fields(
            market, [row for row in form.get("fields") or [] if path_of(row)]
        )
        fields = [{key: copy.deepcopy(row[key]) for key in (*_SCHEMA_KEYS, *_EDITOR_KEYS) if key in row}
                  for row in compacted]
        contracts[market] = {"category_id": form.get("category_id"), "fields": fields,
                             "page_labels": bool(form.get("page_labels")),
                             "inputs": inputs(listing, market, fields)}
    return contracts


def editor_form(listing: dict, market: str, *, values: dict | None = None) -> dict | None:
    """Render a saved definition using this revision's values, without a network read.

    A changed controlling choice needs a new definition; never show the old
    category's dropdowns as if they belonged to the new category.
    """
    from anteroom.services import choice_lists

    contract = (listing.get(KEY) or {}).get(market)
    if not isinstance(contract, dict) or contract.get("unavailable") or "page_labels" not in contract:
        return None
    fields = contract.get("fields") or []
    if not fields or not inputs_match(listing, market, fields, contract.get("inputs")):
        return None
    values = listing if values is None else values
    rows = []
    definitions = {path_of(row): row for row in fields}
    for definition in fields:
        row = choice_lists.hydrate_row(copy.deepcopy(choice_lists.compact_row(market, definition)))
        path = path_of(row)
        value = get_path(values, path)
        if path == "price":
            value = price_for(values, market)
        name = path.rsplit(".", 1)[-1]
        if value is None and path.startswith(f"{market}_specifics.") and name in {"title", "description", "brand"}:
            value = _followed_text(values, market, name)
        if value in (None, "", [], {}) and not is_locked(listing, path):
            value = _first_answer(_row_values(values, market, path, definitions))
            if value in (None, "", [], {}):
                value = shared_answer(values, row, market, path)
            if value in (None, "", [], {}):
                value = settings_answer(values, path)
        row["value"] = copy.deepcopy(value) if value is not None else ""
        rows.append(row)
    return {"marketplace": market, "native": True, "known": True,
            "category_id": contract.get("category_id"), "page_labels": contract["page_labels"],
            "fields": rows}


def prompt(contracts: dict) -> str:
    from anteroom.services.choice_lists import hydrate_row
    from anteroom.services.option_lookup import project_row

    # Presentation metadata belongs to the editor, not the generation context.
    def schema(row, contract):
        hydrated = hydrate_row(row)
        definition = {key: value for key, value in hydrated.items() if key in _SCHEMA_KEYS and key != "choice_list"}
        if path_of(hydrated).startswith("direct_category_ids.") and contract.get("category_id"):
            # Category selection already ran against the complete catalog.
            # Copy generation needs its chosen leaf, not thousands of siblings.
            category_id = str(contract["category_id"])
            values = hydrated.get("option_values") or {}
            options = [label for label in hydrated.get("options") or [] if str(values.get(label, label)) == category_id]
            definition["options"] = options
            definition["option_values"] = {label: values.get(label, label) for label in options}
        definition = project_row(definition)
        return definition

    contracts = {market: {**contract, "fields": [
        schema(row, contract)
        for row in contract.get("fields") or []]} for market, contract in contracts.items()}
    return (
        "\n\n--- Authoritative native marketplace forms ---\n"
        "For these marketplaces, only the paths below may be generated. These native forms override "
        "generic templates, learned fields and Vendoo fields. Never invent a field or dropdown choice. "
        "Use the exact stored ID for a listed choice. Respect types, lengths and selection limits. "
        "Use native dropdown lookup for fields marked options_lookup; leave unsupported choices empty. "
        "Category is already selected; do not change it. If a form is unavailable, omit its marketplace "
        "specifics and leave the draft incomplete. Use photos and seller evidence for product facts; "
        "a dropdown option is not evidence. Omit unsupported facts, including optional ones. "
        "Do not estimate material, fit or measurements to satisfy a required field. "
        "Title and description still follow the canonical listing rules.\n"
        + json.dumps(contracts, ensure_ascii=False)
    )


NOT_AN_OPTION = "Choose a value from this marketplace's current options."


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
        ref = row.get("choice_list")
        if isinstance(ref, dict) and "options" not in row:
            from anteroom.services.choice_lists import allowed_values

            allowed: set[str] | frozenset[str] = allowed_values(str(ref.get("name") or ""))
        else:
            allowed = {str(item) for item in row.get("options") or []}
            allowed.update(str(item) for item in (row.get("option_values") or {}).values())
        if not allowed or any(str(item) not in allowed for item in values):
            return NOT_AN_OPTION
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
    from anteroom.services.marketplace_schemas import aspect_options

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
    for candidate in ("ebay", "poshmark", "mercari", "depop", "etsy"):
        if any(str(row).startswith(f"{candidate}_specifics.") for row in rows):
            return candidate
    return ""


# Etsy keys List sends (etsy_listings.list_fields) that Etsy's form has no row
# for: styles are unconfirmed on the current page (etsy-native-form.json,
# not_offered), so no captured form can check them. Keep the model's answer.
_ETSY_SENT_WITHOUT_ROW = frozenset({"etsy_specifics.styles", "etsy_specifics.style"})

_ETSY_FIELD_ALIASES = {
    "whomade": "etsy_specifics.whoMade",
    "whomadeit": "etsy_specifics.whoMade",
    "whatis": "etsy_specifics.whatIsIt",
    "whatisit": "etsy_specifics.whatIsIt",
    "whenmade": "etsy_specifics.whenMade",
    "whenwasitmade": "etsy_specifics.whenMade",
    "renewaloption": "etsy_specifics.renewalOption",
    "shouldautorenew": "etsy_specifics.renewalOption",
}


def _etsy_native_path(path: str, rows: dict[str, dict]) -> str | None:
    """Map legacy Etsy keys (who_made, what_is, top-level attributes) onto the native form's row path."""
    if not path.startswith("etsy_specifics."):
        return None
    if path in rows:
        return path
    rest = path.removeprefix("etsy_specifics.")
    if "." not in rest:
        mapped = _ETSY_FIELD_ALIASES.get(_fold(rest))
        if mapped and mapped in rows:
            return mapped
        leaf = rest
    elif rest.startswith("category_specifics."):
        leaf = rest.removeprefix("category_specifics.")
    else:
        return None
    if "." in leaf:
        return None
    wanted = _fold(leaf)
    if not wanted:
        return None
    for name, row in rows.items():
        if not name.startswith("etsy_specifics.category_specifics."):
            continue
        if _fold(name.rsplit(".", 1)[-1]) == wanted or _fold(row.get("label") or "") == wanted:
            return name
    return None


def _etsy_canonical_value(path: str, row: dict, value) -> str | None:
    """Map Etsy's known code/alias spellings for who/what/when/renewal to the dropdown label."""
    if path not in {"etsy_specifics.whoMade", "etsy_specifics.whatIsIt",
                    "etsy_specifics.whenMade", "etsy_specifics.renewalOption"}:
        return None
    if isinstance(value, (list, dict)) or value in (None, ""):
        return None
    options = [str(option) for option in row.get("options") or []]
    if not options or str(value) in options:
        return None
    from anteroom.services.etsy_fields import _canonical_etsy_when, etsy_renewal_raw, etsy_what_raw, etsy_who_raw

    if path == "etsy_specifics.whoMade":
        mapped = etsy_who_raw({"who_made": value})
    elif path == "etsy_specifics.whatIsIt":
        mapped = etsy_what_raw({"what_is": value})
    elif path == "etsy_specifics.whenMade":
        mapped = _canonical_etsy_when(str(value))
    else:
        mapped = etsy_renewal_raw({"renewalOption": value})
    return mapped if mapped and mapped in options else None


def _row_values(listing: dict, market: str, path: str, rows: dict[str, dict]) -> list:
    """Every value this listing stores for ``path``, whatever its spelling.

    Only spellings the form does not itself name are folded in: a row keeps the
    value stored at its own path first, and a differently-spelled key is read
    only when that row would otherwise look empty.
    """
    found = [get_path(listing, path)]
    want = _fold(path)
    prefix = f"{market}_specifics"
    block = listing.get(prefix)
    if not isinstance(block, dict):
        return found
    for stored, value in _leaves(block, prefix, set(rows)):
        if stored in rows:
            continue
        if _fold(stored) == want or (market == "etsy" and _etsy_native_path(stored, rows) == path):
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


def _size_spelling(row: dict, value) -> str | None:
    """The field's own spelling of the size ``value`` names, when its list offers it.

    Depop's men's waist is the label 36" where the tag reads 36, and a listing
    save compares the answer with the form's choices. The same size written a
    different way is an answer, not a refused choice, so it keeps its row.
    """
    if isinstance(value, (list, dict)) or not row.get("selection_only"):
        return None
    if "size" not in str(row.get("label") or "").casefold().split() \
            and "size" not in path_of(row).casefold().split("."):
        return None
    options = [str(option) for option in row.get("options") or []]
    if not options or str(value) in options:
        return None
    from anteroom.models.sizes import size_choice

    return size_choice(value, options)


def _form_unavailable(contract: object) -> bool:
    return not isinstance(contract, dict) or bool(contract.get("unavailable")) or not contract.get("fields")


def constrain_generated(
    listing: dict, seed: dict, *, freeze_categories: bool = True, keep_evidence: bool = False,
    use_saved_defaults: bool = False,
) -> dict:
    """Discard model-invented paths and invalid answers; preserve server-owned contracts."""
    from anteroom.services.user_settings import apply_etsy_listing_field_defaults, clear_mercari_answers_with_defaults

    contracts = seed.get(KEY)
    # Captured rows validate Etsy defaults like any other answer. If an Etsy
    # capture failed, apply defaults after the previous answers are restored.
    restore_etsy = isinstance(contracts, dict) and "etsy" in contracts and _form_unavailable(contracts["etsy"])
    if use_saved_defaults and not restore_etsy:
        apply_etsy_listing_field_defaults(listing, seed)
    if not isinstance(contracts, dict):
        listing.pop(KEY, None)
        listing.pop(EVIDENCE_KEY, None)
        if use_saved_defaults:
            clear_mercari_answers_with_defaults(listing, seed)
        return listing
    from anteroom.services import choice_lists

    listing[KEY] = copy.deepcopy(choice_lists.compact_contracts(contracts))
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
        if _form_unavailable(contract):
            # A form that could not be loaded is unknown, not one with no
            # fields: nothing here can be checked. Keep the answers already
            # saved (seed) and drop anything new, which no form has checked.
            # The seed also holds the seller's locked answers.
            saved = seed.get(prefix)
            if isinstance(saved, dict):
                listing[prefix] = copy.deepcopy(saved)
            else:
                listing.pop(prefix, None)
            continue
        if not isinstance(block, dict):
            continue
        rows = {path_of(row): row for row in contract.get("fields") or []}
        for path, value in list(_leaves(block, prefix, set(rows))):
            if is_locked(seed, path):
                set_path(listing, path, copy.deepcopy(get_path(seed, path)))
                continue
            if market == "ebay" and path not in rows:
                native_path = next((name for name in rows if _fold(name) == _fold(path)), None)
                if native_path:
                    # Listing normalization uses sizeType/waistSize; retain
                    # their answers under the native form's own aspect names.
                    _remove(listing, path)
                    if get_path(listing, native_path) in (None, "", [], {}):
                        set_path(listing, native_path, value)
                    path = native_path
                    value = get_path(listing, path)
            elif market == "etsy" and path not in rows:
                native_path = _etsy_native_path(path, rows)
                if native_path:
                    # Fold legacy snake_case and top-level Etsy attributes onto
                    # their native form rows so invalid choices are still dropped.
                    _remove(listing, path)
                    if get_path(listing, native_path) in (None, "", [], {}):
                        set_path(listing, native_path, value)
                    path = native_path
                    value = get_path(listing, path)
            if path in rows:
                spelling = _size_spelling(rows[path], value)
                if spelling is not None:
                    set_path(listing, path, spelling)
                    value = spelling
                if market == "etsy" and path == "etsy_specifics.tags" and isinstance(value, (list, str)):
                    # Drop only the tags Etsy would refuse, as List always has,
                    # instead of failing the whole list on one long tag.
                    from anteroom.services.etsy_fields import sanitize_etsy_tags

                    value = sanitize_etsy_tags(value)
                    set_path(listing, path, value)
                if market == "etsy":
                    canonical = _etsy_canonical_value(path, rows[path], value)
                    if canonical is not None:
                        set_path(listing, path, canonical)
                        value = canonical
            if is_locked(seed, path):
                set_path(listing, path, copy.deepcopy(get_path(seed, path)))
            elif market == "etsy" and path in _ETSY_SENT_WITHOUT_ROW and path not in rows:
                continue
            elif path not in rows or value_error({**applicable_row(rows[path], listing, list(rows.values())), "required": False}, value):
                _remove(listing, path)
    for path in locked_paths(seed):
        if path.split(".", 1)[0] in {f"{market}_specifics" for market in contracts}:
            value = get_path(seed, path)
            if value is not None:
                set_path(listing, path, copy.deepcopy(value))
    if use_saved_defaults:
        if restore_etsy:
            apply_etsy_listing_field_defaults(listing, seed)
        # These are supplied by the form from Settings, so clear any unlocked
        # answers after constraints have had a chance to restore them.
        clear_mercari_answers_with_defaults(listing, seed)
    return listing


def gaps(listing: dict, market: str) -> list[dict]:
    contract = (listing.get(KEY) or {}).get(market)
    if not isinstance(contract, dict):
        return []
    def gap(path, message, *, blocking=True, label=None):
        label = str(label or "").strip()
        return {"field": path, "message": message, "blocking": blocking, **({"label": label} if label else {})}
    if contract.get("unavailable"):
        return [gap(f"{market}_specifics", "The marketplace form could not be loaded. Reload its fields before listing.")]
    fields = contract.get("fields") or []
    if not inputs_match(listing, market, fields, contract.get("inputs")):
        return [gap(f"direct_category_ids.{market}", "The form's category or dependent choices changed. Reload its fields before listing.")]
    found = []
    rows = {path_of(row): row for row in fields}
    folded = {_fold(path) for path in rows}
    for path, value in _leaves(listing.get(f"{market}_specifics") or {}, f"{market}_specifics", set(rows)):
        if path == CANONICAL_PRICE.get(market) and "price" in rows:
            continue
        if path in rows or _fold(path) in folded or (market == "etsy" and _etsy_native_path(path, rows)) or value in (None, "", [], {}):
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
        if path == "price":
            value = price_for(listing, market)
        # Shared prose and price are explicitly offered by the form, but may follow General.
        name = path.rsplit(".", 1)[-1]
        if value is None and name in {"title", "description", "brand"}:
            value = _followed_text(listing, market, name)
        if value in (None, "", [], {}) and not is_locked(listing, path):
            value = _first_answer(_row_values(listing, market, path, rows))
        if value in (None, "", [], {}) and not is_locked(listing, path):
            value = shared_answer(listing, row, market, path)
        if value in (None, "", [], {}) and not is_locked(listing, path):
            value = settings_answer(listing, path)
        if value in (None, "", [], {}) and not is_locked(listing, path) and market == "mercari":
            note, blocking, value = _mercari_label(listing, row, path, value)
            if note:
                found.append(gap(path, note, blocking=blocking, label=row.get("label")))
            if note and blocking:
                continue
        if account_resolved(row) and value in (None, "", [], {}):
            # List picks this from the seller's account, so the row is not
            # empty in any way that stops the listing.
            found.append(gap(path, f"{row.get('label') or path}: Uses the account's own choice.", blocking=False))
            continue
        if market == "etsy":
            canonical = _etsy_canonical_value(path, row, value)
            if canonical is not None:
                value = canonical
        if path == "mercari_specifics.brand":
            from anteroom.services.mercari_form import resolve_brand

            value = resolve_brand(listing)[0]
        error = value_error(applicable_row(row, listing, fields), value)
        if error:
            # A policy or location with no stored answer is chosen from the
            # account at List. Missing one here does not stop the listing, so it
            # is said, not refused.
            found.append(gap(path, f"{row.get('label') or path}: {error}",
                             blocking=not account_resolved(row), label=row.get("label")))
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
    from anteroom.services.marketplace_schemas import _ebay_listing_type

    ebay = listing.get("ebay_specifics")
    if not isinstance(ebay, dict):
        ebay = {}
    return _ebay_listing_type(ebay.get("listingFormat")) == "AUCTION"


# eBay policies and the item location are chosen in Settings, not on the form.
_SETTINGS_CHOICES = {"ebay_specifics.fulfillmentPolicyId": "fulfillment_policy_id",
                     "ebay_specifics.paymentPolicyId": "payment_policy_id",
                     "ebay_specifics.returnPolicyId": "return_policy_id",
                     "ebay_specifics.merchantLocationKey": "merchant_location_key"}
_ETSY_SAVED_DEFAULTS = frozenset({"etsy_specifics.whoMade", "etsy_specifics.whatIsIt",
                                  "etsy_specifics.renewalOption"})


def _mercari_label(listing: dict, row: dict, path: str, value: Any) -> tuple[str | None, bool, Any]:
    """Mercari's empty label row answered the way List answers it: ``(note, blocking, value)``.

    A captured form holds the labels Mercari offered for this package, so a
    saved carrier counts only when that form offers it, under its id for this
    weight tier. When it does not, List sends USPS Ground Advantage if that is
    offered, and the note says so; with neither, the seller has to choose. A
    label the prepared form resolved is checked like any value.
    """
    from anteroom.services import mercari_shipping_label as labels

    # Ship on your own asks for no label, so it has nothing to answer here.
    if path != labels.PATH or not row.get("required") or not labels.needs_label(listing):
        return None, False, value
    answer = labels.label_answer(listing)
    if answer.source == "form":
        return None, False, answer.value
    if answer.source != "default":
        return None, False, value
    offered = labels.offered_id(row, answer.value)
    if offered:
        return None, False, offered
    automatic = labels.offered_id(row, labels.AUTOMATIC)
    if automatic:
        return labels.automatic_instead_message(answer.value), False, automatic
    return labels.unavailable_default_message(answer.value), True, value


def settings_answer(listing: dict, path: str) -> str | None:
    """The value Settings holds for a row the form leaves to the seller's account.

    List already falls back to these defaults; pre-flight reads the same choice
    so a policy set once in Settings is not reported as an empty row.
    """
    if path in _ETSY_SAVED_DEFAULTS:
        from anteroom.services.etsy_fields import DEFAULT_ETSY_RENEWAL
        from anteroom.services.user_settings import get_listing_field_defaults

        saved = get_listing_field_defaults()
        chosen = str((saved.get(path) or {}).get("label") or "").strip()
        if chosen:
            return chosen
        return DEFAULT_ETSY_RENEWAL if path == "etsy_specifics.renewalOption" else None
    setting = _SETTINGS_CHOICES.get(path)
    if not setting:
        return None
    from anteroom.services.user_settings import get_ebay_listing_defaults

    return str(get_ebay_listing_defaults().get(setting) or "").strip() or None


def _followed_text(listing: dict, market: str, name: str) -> str:
    """The title, description or brand a form follows, as that marketplace takes it."""
    if market == "mercari" and name == "brand":
        from anteroom.services.mercari_form import resolve_brand

        return resolve_brand(listing)[0]
    value = marketplace_text(listing, market, name)
    if market == "depop" and name == "brand":
        from anteroom.services.depop_form import listed_brand

        return listed_brand(value)
    return value


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
        from anteroom.services.ebay_listings import _GENERAL_ASPECTS, _aspect_key, _specifics

        # Posting preserves an explicitly stored blank aspect. General only
        # supplies an aspect the marketplace section does not define at all.
        if any(_aspect_key(str(key)) == _aspect_key(name) for key in _specifics(listing)):
            return None

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
    if market == "depop":
        specifics = listing.get("depop_specifics")
        specifics = specifics if isinstance(specifics, dict) else {}
        if path == "depop_specifics.size" and "size" not in specifics and "nativeSizeId" not in specifics:
            from anteroom.services.depop_form import size_label

            category_id = str((listing.get("direct_category_ids") or {}).get("depop") or "")
            return size_label(category_id, marketplace_text(listing, "depop", "size")) or None
        if path == "depop_specifics.condition" and "condition" not in specifics:
            from anteroom.services.depop_form import general_condition

            return general_condition(listing) or None
        if path == "depop_specifics.colour" and "colour" not in specifics:
            from anteroom.services.depop_form import general_colours

            return general_colours(listing) or None
    if market == "mercari":
        specifics = listing.get("mercari_specifics")
        specifics = specifics if isinstance(specifics, dict) else {}
        if path == "mercari_specifics.noBrand" and "noBrand" not in specifics:
            from anteroom.services.mercari_form import resolve_brand

            return resolve_brand(listing)[1]
        if path == "mercari_specifics.condition" and "condition" not in specifics:
            from anteroom.services.mercari_form import general_condition

            return general_condition(listing) or None
        if path == "mercari_specifics.nativeSizeId" and "nativeSizeId" not in specifics:
            from anteroom.services.mercari_form import match_size_id

            category_id = str((listing.get("direct_category_ids") or {}).get("mercari") or "")
            return match_size_id(listing, category_id) or None
    if market == "poshmark":
        specifics = listing.get("poshmark_specifics")
        specifics = specifics if isinstance(specifics, dict) else {}
        if path == "poshmark_specifics.condition" and "condition" not in specifics:
            from anteroom.services.poshmark_form import general_condition

            return general_condition(listing) or None
    if market == "etsy":
        specifics = listing.get("etsy_specifics")
        specifics = specifics if isinstance(specifics, dict) else {}
        if path == "etsy_specifics.whenMade" and not any(
                _fold(key) in {"whenmade", "whenwasitmade"} for key in specifics):
            from anteroom.services.etsy_fields import etsy_when_raw, etsy_when_to_store

            when = etsy_when_raw(specifics, listing)
            if when:
                options = [str(opt) for opt in row.get("options") or []]
                mapped = etsy_when_to_store(when, listing, options)
                if mapped and (not options or mapped in options):
                    return mapped
        if path.startswith("etsy_specifics.category_specifics."):
            nested = specifics.get("category_specifics")
            nested = nested if isinstance(nested, dict) else {}
            folded = _fold(row.get("label") or name)
            if folded in {"primarycolor", "secondarycolor"} and not any(
                    _fold(key) == folded for key in (*nested, *specifics)):
                raw = (listing.get("primaryColor") or listing.get("color")) if folded == "primarycolor" else listing.get("secondaryColor")
                if raw not in (None, "", [], {}):
                    from anteroom.services.etsy_listings import _match

                    choices = [{"label": str(opt)} for opt in row.get("options") or []]
                    values = raw if isinstance(raw, list) else str(raw).split(",")
                    matched: list[str] = []
                    for part in values:
                        text = str(part or "").strip()
                        if not text:
                            continue
                        hit = _match(text, choices) if choices else {"label": text}
                        if hit and hit["label"] not in matched:
                            matched.append(hit["label"])
                    if matched:
                        return matched if row.get("multi") else matched[0]
    return None


def invalidate_dependents(previous: dict, listing: dict) -> None:
    """Clear carried answers when a category or a declared controlling field changes."""
    for market, contract in (previous.get(KEY) or {}).items():
        fields = contract.get("fields") or []
        # Invalidate for this edit, even if the saved form is still stale from
        # an earlier edit. Form freshness is checked separately by gaps().
        changed = changed_inputs(listing, market, fields, inputs(previous, market, fields))
        cleared_by_edit = {
            path
            for row in fields
            if row.get("clears")
            and _answered(get_path(previous, path_of(row))) != _answered(get_path(listing, path_of(row)))
            for path in row.get("clears") or []
        }
        if not changed and not cleared_by_edit:
            continue
        category_changed = any("categor" in path.casefold() for path in changed)
        clear = {path_of(row) for row in fields if category_changed and row.get("category_specific")}
        clear.update(path for row in fields if path_of(row) in changed for path in row.get("clears") or [])
        clear.update(cleared_by_edit)
        changed_labels = {row.get("label") for row in fields if path_of(row) in changed}
        clear.update(path_of(row) for row in fields for rules in (row.get("value_constraints") or {}).values()
                     for rule in rules if rule.get("parentName") in changed_labels)
        for path in clear:
            if get_path(previous, path) == get_path(listing, path) and not is_locked(listing, path):
                _remove(listing, path)
