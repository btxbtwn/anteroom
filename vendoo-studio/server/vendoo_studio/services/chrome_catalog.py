"""Poshmark's and Depop's own categories, kept beside the ones Send to Vendoo uses.

The extension is asked when Chrome is connected, because that is the catalog
and the parcel list a direct List actually uses. A public copy of each category
list fills the same ids when Chrome is not connected, so generation can remember
them. Neither path writes ``marketplace_category_ids``.
"""
from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from types import SimpleNamespace
from typing import Any

import httpx

from vendoo_studio.services.safe_fetch import fetch_public

log = logging.getLogger("vendoo_studio.chrome_catalog")

CHROME_MARKETS = ("poshmark", "depop")
# Depop's USPS parcel titles (the words the listing form stores).
KNOWN_PARCELS = frozenset({
    "extraextrasmall", "extrasmall", "small", "medium", "large", "extralarge",
})
_POSHMARK_URL = "https://poshmark.com/vm-rest/meta/catalog_v2"
_DEPOP_URL = "https://api.depop.com/api/v3/attributes/"
_CATALOG_TTL_S = 6 * 60 * 60
_DEPOP_ID = re.compile(r"^(kidswear|menswear|womenswear|everything-else)_(.+)$")
_cache: dict[str, Any] = {}
_cache_at = 0.0


def name_key(value: Any) -> str:
    """The same comparison the extension uses for Depop names and parcel titles."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "", text.lower().replace("&", "and"))


def poshmark_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def parcel_key(value: Any) -> str:
    return name_key(value)


def clear_catalog_cache() -> None:
    global _cache_at
    _cache.clear()
    _cache_at = 0.0


def _poshmark_body(catalog: dict) -> dict:
    data = catalog.get("data") if isinstance(catalog.get("data"), dict) else catalog
    return data if isinstance(data, dict) else {}


def _poshmark_from_ids(catalog: dict, ids: list) -> dict | None:
    clean = [str(item) for item in ids if str(item or "") and not str(item).startswith("group:")]
    if len(clean) < 2 or len(clean) > 3:
        return None
    body = _poshmark_body(catalog)
    department = next((entry for entry in body.get("catalog", {}).get("departments") or [] if entry.get("id") == clean[0]), None)
    category = next((entry for entry in (department or {}).get("categories") or [] if entry.get("id") == clean[1]), None)
    if not department or not category:
        return None
    feature = None
    if len(clean) == 3 and clean[2] != clean[1]:
        feature = next((entry for entry in category.get("category_features") or [] if entry.get("id") == clean[2]), None)
        if not feature:
            return None
    leaf = feature.get("id") if feature else category.get("id")
    parts = [department.get("id"), category.get("id")]
    if leaf and leaf != category.get("id"):
        parts.append(leaf)
    return {"id": "/".join(str(part) for part in parts if part)}


def _poshmark_names(listing: dict) -> list[str]:
    objects = listing.get("marketplace_category_objects") if isinstance(listing.get("marketplace_category_objects"), dict) else {}
    obj = objects.get("poshmark") if isinstance(objects.get("poshmark"), dict) else {}
    form = listing.get("form") if isinstance(listing.get("form"), dict) else {}
    overrides = form.get("overrides") if isinstance(form.get("overrides"), dict) else {}
    category = overrides.get("categoryV2") if isinstance(overrides.get("categoryV2"), dict) else {}
    names = obj.get("displayPath") if isinstance(obj.get("displayPath"), list) and obj.get("displayPath") else None
    if names is None and isinstance(category.get("displayPath"), list) and category.get("displayPath"):
        names = category.get("displayPath")
    if names is not None:
        return [str(part) for part in names]
    paths = listing.get("marketplace_categories") if isinstance(listing.get("marketplace_categories"), dict) else {}
    return [part.strip() for part in str(paths.get("poshmark") or "").split(">") if part.strip()]


def poshmark_category_id(catalog: dict, listing: dict) -> str:
    """Poshmark's own category id, ``department/category/feature``, when the catalog lists it."""
    if not isinstance(catalog, dict) or not isinstance(listing, dict):
        return ""
    direct = listing.get("direct_category_ids") if isinstance(listing.get("direct_category_ids"), dict) else {}
    raw = direct.get("poshmark")
    direct_ids = raw.split("/") if isinstance(raw, str) else list(raw) if isinstance(raw, list) else []
    found = _poshmark_from_ids(catalog, direct_ids)
    if not found:
        form = listing.get("form") if isinstance(listing.get("form"), dict) else {}
        overrides = form.get("overrides") if isinstance(form.get("overrides"), dict) else {}
        category = overrides.get("categoryV2") if isinstance(overrides.get("categoryV2"), dict) else {}
        objects = listing.get("marketplace_category_objects") if isinstance(listing.get("marketplace_category_objects"), dict) else {}
        obj = objects.get("poshmark") if isinstance(objects.get("poshmark"), dict) else {}
        path = category.get("path") if isinstance(category.get("path"), list) else obj.get("path")
        found = _poshmark_from_ids(catalog, path if isinstance(path, list) else [])
    if not found:
        names = _poshmark_names(listing)
        body = _poshmark_body(catalog)
        if len(names) >= 2:
            department = next((entry for entry in body.get("catalog", {}).get("departments") or []
                               if poshmark_key(entry.get("display")) == poshmark_key(names[0])), None)
            category = next((entry for entry in (department or {}).get("categories") or []
                             if poshmark_key(entry.get("display")) == poshmark_key(names[1])), None)
            if department and category and len(names) == 2:
                found = _poshmark_from_ids(catalog, [department.get("id"), category.get("id")])
            elif department and category:
                feature = next((entry for entry in category.get("category_features") or []
                                if poshmark_key(entry.get("display")) == poshmark_key(names[2])), None)
                if feature:
                    found = _poshmark_from_ids(catalog, [department.get("id"), category.get("id"), feature.get("id")])
    return str((found or {}).get("id") or "")


def _depop_product(attributes: dict, department: str, product_type: str) -> str:
    if not department or not product_type:
        return ""
    if not any(isinstance(entry, dict) and entry.get("id") == department for entry in attributes.get("department") or []):
        return ""
    for group in attributes.get("group") or []:
        if not isinstance(group, dict):
            continue
        departments = group.get("department")
        if isinstance(departments, list) and department not in departments:
            continue
        types = group.get("product_types") or []
        if any(isinstance(entry, dict) and entry.get("id") == product_type for entry in types):
            return f"{department}_{product_type}"
    return ""


def _depop_from_text(attributes: dict, raw: str) -> str:
    match = _DEPOP_ID.fullmatch(str(raw or "").strip())
    return _depop_product(attributes, match.group(1), match.group(2)) if match else ""


def _depop_by_name(attributes: dict, listing: dict) -> str:
    objects = listing.get("marketplace_category_objects") if isinstance(listing.get("marketplace_category_objects"), dict) else {}
    obj = objects.get("depop") if isinstance(objects.get("depop"), dict) else {}
    paths = listing.get("marketplace_categories") if isinstance(listing.get("marketplace_categories"), dict) else {}
    text = " > ".join(str(part) for part in obj.get("displayPath")) if isinstance(obj.get("displayPath"), list) else str(paths.get("depop") or "")
    parts = [part.strip() for part in text.split(">") if part.strip()]
    leaf = name_key(parts[-1] if parts else "")
    head = name_key(parts[0] if parts else "")
    department = "kidswear" if "kid" in head else "womenswear" if re.search(r"women|female", head) else "menswear" if re.search(r"men|male", head) else ""
    if not leaf:
        return ""
    hits = []
    for group in attributes.get("group") or []:
        if not isinstance(group, dict):
            continue
        for product in group.get("product_types") or []:
            if not isinstance(product, dict):
                continue
            label = str(product.get("name_i18n", {}).get("en-US") if isinstance(product.get("name_i18n"), dict) else product.get("name") or product.get("id") or "")
            if name_key(product.get("id")) != leaf and name_key(label) != leaf:
                continue
            for department_id in group.get("department") or []:
                if not department or department_id == department:
                    found = _depop_product(attributes, str(department_id), str(product.get("id") or ""))
                    if found:
                        hits.append(found)
    unique = list(dict.fromkeys(hits))
    return unique[0] if len(unique) == 1 else ""


def depop_category_id(attributes: dict, listing: dict) -> str:
    """Depop's own ``department_productType`` when that product type is still in its list."""
    if not isinstance(attributes, dict) or not isinstance(listing, dict):
        return ""
    direct = listing.get("direct_category_ids") if isinstance(listing.get("direct_category_ids"), dict) else {}
    specifics = listing.get("depop_specifics") if isinstance(listing.get("depop_specifics"), dict) else {}
    ids = listing.get("marketplace_category_ids") if isinstance(listing.get("marketplace_category_ids"), dict) else {}
    for raw in (direct.get("depop"), specifics.get("categoryId"), ids.get("depop")):
        found = _depop_from_text(attributes, str(raw or ""))
        if found:
            return found
    form = listing.get("form") if isinstance(listing.get("form"), dict) else {}
    overrides = form.get("overrides") if isinstance(form.get("overrides"), dict) else {}
    category = overrides.get("categoryV2") if isinstance(overrides.get("categoryV2"), dict) else {}
    objects = listing.get("marketplace_category_objects") if isinstance(listing.get("marketplace_category_objects"), dict) else {}
    obj = objects.get("depop") if isinstance(objects.get("depop"), dict) else {}
    resolved = category or obj
    leaf = str(resolved.get("id") or "")
    chain = [str(part) for part in resolved.get("path")] if isinstance(resolved.get("path"), list) else []
    department = str(chain[0] if chain else leaf.split("_")[0] if leaf else "")
    parent = chain[-2] if len(chain) > 2 else ""
    extras = resolved.get("extras") if isinstance(resolved.get("extras"), dict) else {}
    candidates = [
        extras.get("productType"),
        leaf[len(parent) + 1:] if parent and leaf.startswith(f"{parent}_") else "",
        leaf[len(department) + 1:] if department and leaf.startswith(f"{department}_") else "",
    ]
    for product_type in candidates:
        found = _depop_product(attributes, department, str(product_type or ""))
        if found:
            return found
    return _depop_by_name(attributes, listing)


def resolved_from_catalogs(listing: dict, catalogs: dict | None) -> dict[str, str]:
    """Category ids the public catalogs can name. Empty when a catalog was not loaded."""
    catalogs = catalogs or {}
    found = {}
    poshmark = poshmark_category_id(catalogs.get("poshmark") or {}, listing)
    depop = depop_category_id(catalogs.get("depop") or {}, listing)
    if poshmark:
        found["poshmark"] = poshmark
    if depop:
        found["depop"] = depop
    return found


def apply_direct_ids(listing: dict, categories: dict | None) -> bool:
    """Remember Poshmark's and Depop's ids on the listing. Vendoo's ids stay as they are."""
    if not isinstance(listing, dict) or not isinstance(categories, dict):
        return False
    current = dict(listing.get("direct_category_ids")) if isinstance(listing.get("direct_category_ids"), dict) else {}
    changed = False
    for key in CHROME_MARKETS:
        value = str(categories.get(key) or "").strip()
        if value and current.get(key) != value:
            current[key] = value
            changed = True
    if changed:
        listing["direct_category_ids"] = current
    return changed


def keep_direct_categories(previous: dict, incoming: dict) -> None:
    """A save from the editor keeps a category Chrome already resolved."""
    if not isinstance(previous, dict) or not isinstance(incoming, dict):
        return
    old = previous.get("direct_category_ids") if isinstance(previous.get("direct_category_ids"), dict) else {}
    if not any(str(old.get(key) or "").strip() for key in CHROME_MARKETS):
        return
    if not isinstance(incoming.get("direct_category_ids"), dict):
        incoming["direct_category_ids"] = dict(old)
        return
    merged = dict(incoming["direct_category_ids"])
    for key in CHROME_MARKETS:
        if str(old.get(key) or "").strip() and not str(merged.get(key) or "").strip():
            merged[key] = old[key]
    incoming["direct_category_ids"] = merged


def remember_direct_categories(db, revision, categories: dict | None) -> None:
    """Write the resolved ids onto the current revision, without a new revision."""
    if revision is None or not isinstance(getattr(revision, "listing_json", None), dict) or not categories:
        return
    updated = dict(revision.listing_json)
    if not apply_direct_ids(updated, categories):
        return
    revision.listing_json = updated
    db.commit()


def apply_preview(found: list[dict], preview: dict | None, marketplace: str) -> list[dict]:
    """Add the size, category, and parcel refusals Chrome found. A live parcel match wins."""
    if not isinstance(preview, dict):
        return found
    gaps = preview.get("gaps") if isinstance(preview.get("gaps"), dict) else {}
    extra = []
    for item in gaps.get(marketplace) or []:
        if isinstance(item, dict) and str(item.get("field") or "") and str(item.get("message") or ""):
            extra.append({
                "field": str(item["field"]), "message": str(item["message"]), "blocking": bool(item.get("blocking", True)),
            })
    if marketplace == "depop" and preview.get("parcels_checked") and not any(
        item["field"] == "depop_specifics.parcelSize" and "does not offer" in item["message"] for item in extra
    ):
        found = [item for item in found if not (item["field"] == "depop_specifics.parcelSize" and "does not offer" in item["message"])]
    seen = {(item["field"], item["message"]) for item in found}
    for item in extra:
        key = (item["field"], item["message"])
        if key not in seen:
            found.append(item)
            seen.add(key)
    found.sort(key=lambda item: not item["blocking"])
    return found


def preview_categories(preview: dict | None) -> dict[str, str]:
    raw = preview.get("categories") if isinstance(preview, dict) and isinstance(preview.get("categories"), dict) else {}
    return {key: str(raw.get(key)).strip() for key in CHROME_MARKETS if str(raw.get(key) or "").strip()}


async def live_preview(listing: dict) -> dict | None:
    """Ask the connected extension. A stand-in connection answers nothing."""
    from vendoo_studio.routes.extension import extension_manager
    from vendoo_studio.services import browser_bridge

    if not getattr(extension_manager, "connected", False) or not hasattr(extension_manager, "register_wait"):
        return None
    try:
        result = await browser_bridge.request(
            SimpleNamespace(id=None), "marketplace.direct_check", {"listing": listing}, timeout=25,
        )
    except Exception as exc:  # noqa: BLE001 - List still checks inside Chrome when this read fails
        log.info("No direct catalog check: %s", exc)
        return None
    return result if isinstance(result, dict) and result.get("ok") else None


async def _fetch_json(url: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
            body, _content_type, _url = await fetch_public(
                client, url, accept=lambda kind: not kind or "json" in kind or kind.startswith("text/"),
            )
        parsed = json.loads(body)
    except Exception as exc:  # noqa: BLE001 - generation continues, and Chrome can still resolve the category
        log.info("No public catalog %s: %s", url, exc)
        return None
    return parsed if isinstance(parsed, dict) else None


async def ensure_catalogs() -> dict:
    """Poshmark's and Depop's public category lists, kept for a few hours."""
    global _cache_at
    if _cache and time.monotonic() - _cache_at < _CATALOG_TTL_S:
        return dict(_cache)
    poshmark = await _fetch_json(_POSHMARK_URL)
    depop = await _fetch_json(_DEPOP_URL)
    fetched: dict[str, Any] = {}
    if isinstance(poshmark, dict) and (poshmark.get("catalog") or (isinstance(poshmark.get("data"), dict) and poshmark["data"].get("catalog"))):
        fetched["poshmark"] = poshmark.get("data") if isinstance(poshmark.get("data"), dict) and poshmark["data"].get("catalog") else poshmark
    if isinstance(depop, dict) and isinstance(depop.get("group"), list) and isinstance(depop.get("department"), list):
        fetched["depop"] = depop
    if fetched:
        _cache.clear()
        _cache.update(fetched)
        _cache_at = time.monotonic()
    return dict(_cache)
