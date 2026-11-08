"""Marketplace choice lists that are the same for every listing.

A listing's form names each of these lists instead of carrying it, so a client
downloads the list once and keeps it. Mercari's brands alone are 1.25 MB, which
every Mercari form used to carry.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from functools import cache


def _sources() -> dict[str, tuple[str, str, Callable[[], dict]]]:
    from vendoo_studio.services import depop_form, etsy_form, mercari_form, poshmark_form

    return {
        "mercari-brands": ("mercari", "mercari_specifics.brand", mercari_form.brand_choices),
        "mercari-categories": ("mercari", "direct_category_ids.mercari", mercari_form.category_choices),
        "depop-brands": ("depop", "depop_specifics.brand", depop_form.brand_choices),
        "depop-categories": ("depop", "direct_category_ids.depop", depop_form.category_choices),
        "poshmark-categories": ("poshmark", poshmark_form.CATEGORY_PATH, poshmark_form.category_choices),
        "etsy-categories": ("etsy", etsy_form.CATEGORY_PATH, etsy_form.category_choices),
    }


@cache
def choice_list(name: str) -> tuple[bytes, str] | None:
    """The list as JSON and its version, or None for an unknown name."""
    source = _sources().get(name)
    if source is None:
        return None
    body = json.dumps(source[2](), ensure_ascii=False, separators=(",", ":")).encode()
    return body, hashlib.sha256(body).hexdigest()[:16]


def share(form: dict) -> dict:
    """The form with each shared list replaced by its name and version.

    A row whose choices differ from the shared list (a builder narrowed them for
    this listing) keeps its own."""
    named = {(marketplace, path): (name, load) for name, (marketplace, path, load) in _sources().items()}
    rows = []
    for row in form.get("fields") or []:
        found = named.get((str(form.get("marketplace") or ""), str(row.get("path") or "")))
        if found:
            name, load = found
            payload = load()
            listed = choice_list(name)
            if listed and all(row.get(key) == value for key, value in payload.items()):
                row = {key: value for key, value in row.items() if key not in payload}
                row["choice_list"] = {"name": name, "version": listed[1]}
        rows.append(row)
    return {**form, "fields": rows}
