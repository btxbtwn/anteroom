"""Each marketplace's own categories, kept beside the Vendoo ones.

Send to Vendoo keeps ``marketplace_category_ids`` and Vendoo's field names.
Direct List, generation, and the eBay and Etsy editor sections read
``direct_category_ids``: the leaf each site's own taxonomy names for this item,
and that leaf's own fields.
"""
from __future__ import annotations

import copy
import logging
import re
from typing import Any

log = logging.getLogger("vendoo_studio.direct_categories")

_ID = re.compile(r"[1-9]\d{0,14}")


def search_text(listing: dict, evidence: str = "") -> str:
    """The few words eBay and Etsy can match a category from."""
    title = " ".join(str(listing.get("title") or "").split())
    if title.casefold() in {"draft listing", "new listing"}:
        title = ""
    paths = listing.get("marketplace_categories") if isinstance(listing.get("marketplace_categories"), dict) else {}
    path = ""
    for key in ("ebay", "etsy"):
        path = str(paths.get(key) or "")
        if path:
            break
    if not path:
        path = str(listing.get("category_path") or "")
    parts = [part.strip() for part in path.split(">") if part.strip()]
    context = " ".join(parts[1:] if len(parts) > 1 else parts)
    text = " ".join(part for part in (context, title) if part)
    if not text:
        text = " ".join(str(evidence or "").split())
    return text[:80]


def direct_category_id(listing: dict, marketplace: str) -> str:
    raw = listing.get("direct_category_ids")
    if not isinstance(raw, dict):
        return ""
    text = str(raw.get(marketplace) or "").strip()
    return text if _ID.fullmatch(text) else ""


def live_category_id(listing: dict, marketplace: str) -> str:
    """The leaf whose live schema applies: the direct one, else for eBay the Vendoo id (eBay's own ids)."""
    chosen = direct_category_id(listing, marketplace)
    if chosen or marketplace != "ebay":
        return chosen
    ids = listing.get("marketplace_category_ids")
    text = str(ids.get("ebay") or "").strip() if isinstance(ids, dict) else ""
    return text if _ID.fullmatch(text) else ""


def pick_ebay_suggestion(body: dict) -> dict[str, str] | None:
    """eBay's first suggestion: id and the path from the root down to that leaf."""
    suggestions = body.get("categorySuggestions") if isinstance(body, dict) else None
    if not suggestions:
        return None
    first = suggestions[0] if isinstance(suggestions[0], dict) else {}
    category = first.get("category") if isinstance(first.get("category"), dict) else {}
    if not category.get("categoryId"):
        return None
    ancestors = first.get("categoryTreeNodeAncestors") or []
    names = [str(item.get("categoryName") or "").strip() for item in reversed(ancestors) if isinstance(item, dict)]
    names.append(str(category.get("categoryName") or "").strip())
    names = [name for name in names if name]
    return {"id": str(category["categoryId"]), "path": " > ".join(names)}


def _tokens(text: str) -> list[str]:
    text = re.sub(r"\bt[\s-]?shirts?\b", "tshirt", text, flags=re.I)
    return [word for word in re.findall(r"[a-z0-9]+", text.lower()) if len(word) > 2]


def pick_etsy_leaf(leaves: list[dict], query: str) -> dict[str, str] | None:
    """Match the product type at the leaf first, then its department and ancestors."""
    tokens = {word.rstrip("s") for word in _tokens(query)}
    if not tokens:
        return None
    product_tokens = {word.rstrip("s") for word in _tokens(query.split(">")[-1])}
    ranked: list[tuple[int, int, int, int, dict]] = []
    for leaf in leaves:
        path = str(leaf.get("path") or "")
        words = {word.rstrip("s") for word in _tokens(path)}
        leaf_words = {word.rstrip("s") for word in _tokens(path.split(">")[-1])}
        score = len(tokens & words)
        product_score = len(product_tokens & words)
        if product_score:
            ranked.append((len(product_tokens & leaf_words), product_score, score, len(path), leaf))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[:4], reverse=True)
    leaf = ranked[0][4]
    return {"id": str(leaf["id"]), "path": str(leaf.get("path") or leaf["id"])}


def _fold(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def merge_live_fields(vendoo_fields: list[dict], live_fields: list[dict], listing: dict, marketplace: str) -> list[dict]:
    """Live names, required flags, and choices, laid over Vendoo's fields.

    A field Vendoo already has keeps Vendoo's key, so Send still finds the value.
    A field only the live schema has is added under the live name, which is what
    direct List sends. eBay's condition list is not copied onto Vendoo's condition.
    """
    from vendoo_studio.services.fill_log import listing_value_for_field
    from vendoo_studio.services.form_schema import field_applies, live_belongs_on_form

    by_fold: dict[str, dict] = {}
    for row in vendoo_fields:
        by_fold.setdefault(_fold(str(row.get("label") or "")), row)
        by_fold.setdefault(_fold(str(row.get("key") or "")), row)
    for live in live_fields:
        if str(live.get("id") or "") == "condition" or not live.get("name"):
            continue
        options = [str(value.get("label") or "") for value in live.get("values") or [] if value.get("label")]
        rules = {
            "required": bool(live.get("required")), "multi": bool(live.get("multi")),
            "selection_only": live.get("mode") == "selection", "options": options,
            "max_values": live.get("max_values"), "max_length": live.get("max_length"),
            "options_source": "marketplace",
            "on_form": live_belongs_on_form(live) and field_applies(str(live["name"]), listing),
        }
        hit = by_fold.get(_fold(str(live["name"])))
        if hit:
            hit.update(rules)
            continue
        row = {
            "key": str(live["name"]),
            "label": str(live["name"]),
            "value": listing_value_for_field(listing, marketplace, str(live["name"])) or "",
            **rules,
        }
        vendoo_fields.append(row)
        by_fold[_fold(row["label"])] = row
    return vendoo_fields


async def overlay_live_fields(listing: dict, marketplace: str, fields: list[dict]) -> list[dict]:
    """The editor's rows for one marketplace, with the live schema merged when it is cached or readable."""
    if marketplace not in ("ebay", "etsy"):
        return fields
    category_id = live_category_id(listing, marketplace)
    if not category_id:
        return fields
    from vendoo_studio.services import marketplace_schemas

    try:
        live = await marketplace_schemas.fields(marketplace, category_id)
    except Exception as exc:  # noqa: BLE001 - the Vendoo form still renders
        log.info("No live %s fields for %s: %s", marketplace, category_id, exc)
        return fields
    return merge_live_fields(fields, live, listing, marketplace)


def live_fields_prompt(listing: dict) -> str:
    """Exact eBay and Etsy field names for generation. Empty when the live schema is not cached yet."""
    from vendoo_studio.services.category_fields import load_rows
    from vendoo_studio.services.marketplace_schemas import EBAY_ASPECTS, ETSY_PROPERTIES

    ids = listing.get("direct_category_ids") if isinstance(listing.get("direct_category_ids"), dict) else {}
    blocks = []
    for marketplace, cache_key, bucket in (
        ("ebay", EBAY_ASPECTS, "ebay_specifics"),
        ("etsy", ETSY_PROPERTIES, "etsy_specifics.category_specifics"),
    ):
        category_id = live_category_id(listing, marketplace)
        rows = load_rows(cache_key, category_id) if category_id else None
        if not rows:
            continue
        lines = []
        for field in rows:
            name = str(field.get("name") or "").strip()
            if not name:
                continue
            line = f"- {name}" + (" (required)" if field.get("required") else "")
            labels = [str(value.get("label") or "") for value in (field.get("values") or []) if value.get("label")]
            if labels:
                shown = ", ".join(labels[:8])
                line += f" — {shown}" + ("…" if len(labels) > 8 else "")
            lines.append(line)
            if len(lines) == 40:
                break
        path = str(ids.get(f"{marketplace}_path") or category_id)
        blocks.append(
            f"{marketplace.title()} category {path} (id {category_id}). "
            f"Fill these under {bucket} using these exact names:\n" + "\n".join(lines)
        )
    if not blocks:
        return ""
    return (
        "\n\n--- eBay and Etsy category fields ---\n"
        "These are the fields eBay and Etsy list for this category. Fill them under the names above. "
        "Keep filling Vendoo's own field names as well; Send to Vendoo reads those, not these.\n\n"
        + "\n\n".join(blocks)
    )


async def _ebay_leaf(query: str) -> dict[str, str] | None:
    from vendoo_studio.services import marketplace_schemas
    from vendoo_studio.services.ebay_client import EbayClient

    client = await EbayClient.connect()
    body = await client.request(
        "GET", "/commerce/taxonomy/v1/category_tree/0/get_category_suggestions", params={"q": query}, app=True,
    )
    hit = pick_ebay_suggestion(body if isinstance(body, dict) else {})
    if not hit:
        return None
    try:
        await marketplace_schemas.ebay_aspects(client, hit["id"])
    except Exception as exc:  # noqa: BLE001 - the category still stands; the editor can read the schema later
        log.info("No eBay aspects for %s: %s", hit["id"], exc)
    return hit


async def _etsy_leaf(query: str) -> dict[str, str] | None:
    from vendoo_studio.services import marketplace_schemas

    hit = pick_etsy_leaf(list(await marketplace_schemas.etsy_taxonomy()), query)
    if not hit:
        return None
    try:
        await marketplace_schemas.etsy_properties(hit["id"])
    except Exception as exc:  # noqa: BLE001 - same as eBay: the leaf is still the one direct List uses
        log.info("No Etsy properties for %s: %s", hit["id"], exc)
    return hit


async def attach_direct_categories(db, listing: dict, evidence: str = "") -> dict[str, Any]:
    """Choose the live leaves and remember them on the listing. Vendoo's category ids stay as they are."""
    current = listing.get("direct_category_ids")
    current = dict(current) if isinstance(current, dict) else {}
    query = search_text(listing, evidence)
    if not query:
        return current
    paths = listing.get("marketplace_categories") if isinstance(listing.get("marketplace_categories"), dict) else {}
    etsy_query = str(paths.get("etsy") or listing.get("category_path") or query)
    cache_query = "\n".join((query, etsy_query))
    chosen = dict(current)
    if chosen.get("query") != cache_query:
        for key in ("ebay", "ebay_path", "etsy", "etsy_path", "poshmark", "depop", "mercari"):
            chosen.pop(key, None)
    markets = {market for market in ("ebay", "poshmark", "depop", "mercari")
               if not direct_category_id({"direct_category_ids": chosen}, market)}
    if markets:
        from vendoo_studio.services.agent_categories import apply_agent_categories, ebay_tree_leaves
        from vendoo_studio.services.native_category_match import native_rows

        # Each form's own options, matched the same way as a category change in
        # the editor. A known eBay leaf needs no network search.
        # A copy, so filling a category here does not clear the sizes generation wrote.
        apply_agent_categories({**copy.deepcopy(listing), "direct_category_ids": chosen},
                               {**native_rows(), "ebay": ebay_tree_leaves(db)}, markets=markets)
    if not direct_category_id({"direct_category_ids": chosen}, "ebay"):
        try:
            hit = await _ebay_leaf(query)
        except Exception as exc:  # noqa: BLE001 - generation continues on Vendoo's category
            log.info("No live eBay category: %s", exc)
            hit = None
        if hit:
            chosen["ebay"] = hit["id"]
            chosen["ebay_path"] = hit["path"]
    if not direct_category_id({"direct_category_ids": chosen}, "etsy"):
        try:
            hit = await _etsy_leaf(etsy_query)
        except Exception as exc:  # noqa: BLE001 - generation continues on Vendoo's category
            log.info("No live Etsy category: %s", exc)
            hit = None
        if hit:
            chosen["etsy"] = hit["id"]
            chosen["etsy_path"] = hit["path"]
    if any(chosen.get(market) for market in ("ebay", "etsy", "poshmark", "depop", "mercari")):
        chosen["query"] = cache_query
        listing["direct_category_ids"] = chosen
    return chosen
