"""Current editor definitions for chat, including empty and optional fields."""
from __future__ import annotations

import asyncio
import copy
import json
import re

from sqlalchemy.orm import Session
from types import SimpleNamespace

from vendoo_studio.config import skills_dir
from vendoo_studio.repositories.queries import ListingRepo
from vendoo_studio.services import native_contract, native_field_fill, vinted_browser
from vendoo_studio.services.marketplaces import get_selected_marketplaces
from vendoo_studio.services.seller_overrides import get_path


async def forms_prompt(db: Session, conv_id: str) -> str:
    revisions = ListingRepo(db).get_revisions(conv_id)
    if not revisions or not isinstance(revisions[0].listing_json, dict):
        return ""
    listing = copy.deepcopy(revisions[0].listing_json)
    definitions = json.loads((skills_dir() / "list-this/references/listing-editor-fields.json").read_text())
    # Vinted is always a visible editor tab, regardless of Vendoo selections.
    markets = set(get_selected_marketplaces()) | {"vinted"} | set(listing.get(native_contract.KEY) or {})
    db.commit()
    native = await native_field_fill.load_forms(db, conv_id, markets)
    forms = {"general": {"fields": _values(definitions["general"], listing, "general")}}
    for market in sorted(markets):
        if market in native_field_fill.MARKETS:
            form = native.get(market)
            forms[market] = ({"fields": [
                {key: copy.deepcopy(value) for key, value in row.items()
                 if key in (*native_contract._SCHEMA_KEYS, "value")}
                for row in form.get("fields") or []
            ]} if form and form.get("fields") else {"unavailable": True})
            continue
        fields = copy.deepcopy(definitions["shared"])
        if market == "etsy":
            fields += copy.deepcopy(definitions["package"] + definitions["etsy"])
            fields += [{**row, "key": f"etsy_specifics.category_specifics.{row['key']}"}
                       for row in definitions["etsy_optionals"]]
        elif market == "vinted":
            fields = [row for row in fields if row["key"] != "category_path"] + copy.deepcopy(definitions["vinted"])
        fields += await _category_fields(listing, market)
        # Preserve access to existing free-text specifics as the editor does.
        specifics = listing.get(f"{market}_specifics") or {}
        if isinstance(specifics, dict):
            fields += [{"key": f"{market}_specifics.{key}", "label": key}
                       for key, value in specifics.items() if not isinstance(value, dict)
                       and key.casefold() not in {"categoryid", "category"}
                       and f"{market}_specifics.{key}" not in {row["key"] for row in fields}]
        for row in fields:
            key = row["key"]
            if key == "price":
                row["key"] = definitions["marketplace_price_keys"].get(market, key)
            elif "." not in key and key in definitions["followed_general"]:
                row["key"] = f"{market}_specifics.{key}"
        unique = {row["key"]: row for row in fields}
        forms[market] = {"fields": _values(list(unique.values()), listing, market)}
    await _vinted_choices(forms["vinted"], listing)
    return (
        "\n\n--- Current editable forms ---\n"
        "These current editor forms override historical, reference and saved native form instructions above. "
        "Every row is accessible, including optional and empty fields. Use fields rows with path and value "
        "to save changes. Copy an exact choice; where option_values maps labels to IDs, save its ID. "
        "Respect locked fields, types, limits and dependent choices. Category changes are allowed when requested; "
        "change the category first and reload its form before choosing dependent values. "
        "Unavailable forms or choices require a connection or reload; never invent their fields or IDs, "
        "and do not substitute historical or reference options for an unavailable live picker. "
        "Preserve unrelated fields and seller decisions. Options are not evidence of product facts. "
        "Saving updates Anteroom only. Never publish or send to Vendoo.\n"
        + json.dumps(forms, ensure_ascii=False)
    )


def _values(fields: list[dict], listing: dict, market: str) -> list[dict]:
    from vendoo_studio.models.listing_values import marketplace_dropdown_forms

    static = marketplace_dropdown_forms().get("vendoo" if market == "general" else market) or {}

    def fold(text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", text.casefold())

    choices = {fold(key): values for key, values in static.items()}
    aliases = {"size": "ussize", "primarycolor": "primarycolor", "secondarycolor": "secondarycolor"}
    result = []
    for field in fields:
        row = {**field, "path": field["key"], "value": get_path(listing, field["key"])}
        if row["value"] is None and field["key"].startswith(f"{market}_specifics."):
            leaf = field["key"].removeprefix(f"{market}_specifics.")
            if leaf in {"title", "description", "brand", "size", "department", "price"}:
                row["value"] = listing.get(leaf)
        key = fold(field["key"].split(".")[-1])
        options = choices.get(aliases.get(key, key) if market == "general" else key)
        if options and not row.get("options"):
            row.update(options=options, options_source="reference")
        result.append(row)
    return result


async def _category_fields(listing: dict, market: str) -> list[dict]:
    from vendoo_studio.models.listing_values import marketplace_dropdown_forms
    from vendoo_studio.services.category_fields import listing_category_ids
    from vendoo_studio.services.form_schema import field_applies, labels_for_spec
    from vendoo_studio.services.vendoo_create import LOOKUP_TIMEOUT_SEC, fetch_leaf_specifics

    static = marketplace_dropdown_forms().get(market) or {}
    category = listing_category_ids(listing).get(market)
    objects = listing.get("marketplace_category_objects") or {}
    specs = await fetch_leaf_specifics(SimpleNamespace(id=None), market, category, objects.get(market),
                                      timeout=LOOKUP_TIMEOUT_SEC) if category else {}
    fields = []
    for spec in (specs or {}).values():
        if field_applies(spec.display, listing):
            fields.append({"key": f"{market}_specifics.{spec.key}", "label": spec.display,
                           "required": spec.required, "multi": spec.multi,
                           "selection_only": spec.selection_only,
                           "options": labels_for_spec(spec, listing) or static.get(spec.key, [])})
    known = {row["key"] for row in fields}
    fields += [{"key": f"{market}_specifics.{key}", "label": key, "options": options,
                "options_source": "reference"}
               for key, options in static.items() if f"{market}_specifics.{key}" not in known]
    if market == "etsy":
        from vendoo_studio.services.direct_categories import overlay_live_fields

        raw = [{**row, "key": row["key"].removeprefix("etsy_specifics.")} for row in fields]
        fields = [{**row, "key": f"etsy_specifics.{row['key']}"}
                  for row in await overlay_live_fields(listing, market, raw)]
    return fields


async def _vinted_choices(form: dict, listing: dict) -> None:
    rows = {row["path"]: row for row in form["fields"]}
    category = rows["vinted_specifics.category"]
    category["selection_only"] = True
    try:
        catalog = await asyncio.to_thread(vinted_browser.category_rows)
        category["options"] = vinted_browser.category_options(catalog)
        if not category["options"]:
            raise vinted_browser.VintedConnectionError("Vinted returned no category choices.")
        category["options_source"] = "marketplace"
    except vinted_browser.VintedConnectionError as exc:
        category.pop("options", None)
        category["options_source"] = "unavailable"
        category["options_unavailable"] = str(exc)
    brand = rows["vinted_specifics.brand"]
    brand["selection_only"] = True
    query = str(get_path(listing, "vinted_specifics.brand") or listing.get("brand") or "")
    if query:
        try:
            brand["options"] = await asyncio.to_thread(vinted_browser.brands, query, category.get("value") or "")
            brand["options_source"] = "marketplace"
        except vinted_browser.VintedConnectionError as exc:
            brand.pop("options", None)
            brand["options_source"] = "unavailable"
            brand["options_unavailable"] = str(exc)
