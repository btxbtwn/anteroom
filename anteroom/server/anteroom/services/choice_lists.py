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
    from anteroom.services import depop_form, etsy_form, mercari_form, poshmark_form

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


def _named_sources() -> dict[tuple[str, str], tuple[str, Callable[[], dict]]]:
    return {(marketplace, path): (name, load) for name, (marketplace, path, load) in _sources().items()}


def _compact_row(marketplace: str, row: dict, named: dict[tuple[str, str], tuple[str, Callable[[], dict]]]) -> dict:
    if not isinstance(row, dict):
        return row
    found = named.get((str(marketplace or ""), str(row.get("path") or row.get("key") or "")))
    if not found:
        return row
    name, load = found
    listed = choice_list(name)
    if listed is None:
        return row
    existing = row.get("choice_list")
    if isinstance(existing, dict) and existing.get("name") == name and "options" not in row and "option_values" not in row:
        if existing.get("version") == listed[1]:
            return row
        return {**row, "choice_list": {"name": name, "version": listed[1]}}
    payload = load()
    if all(row.get(key) == value for key, value in payload.items()):
        compacted = {key: value for key, value in row.items() if key not in payload}
        compacted["choice_list"] = {"name": name, "version": listed[1]}
        return compacted
    return row


def compact_row(marketplace: str, row: dict) -> dict:
    """Replace a row's static shared choice list with its ``choice_list`` reference."""
    return _compact_row(marketplace, row, _named_sources())


def compact_fields(marketplace: str, fields: list[dict]) -> list[dict]:
    """Return ``fields`` with static shared choice lists replaced by compact references."""
    if not fields:
        return fields
    named = _named_sources()
    rows = []
    changed = False
    for row in fields:
        compacted = _compact_row(marketplace, row, named)
        if compacted is not row:
            changed = True
        rows.append(compacted)
    return rows if changed else fields


def compact_contracts(contracts: dict) -> dict:
    """Return a ``_native_forms`` dict with static shared choice lists compacted."""
    if not isinstance(contracts, dict):
        return contracts
    result = {}
    changed = False
    for market, contract in contracts.items():
        if not isinstance(contract, dict) or not contract.get("fields"):
            result[market] = contract
            continue
        fields = contract["fields"]
        compacted = compact_fields(market, fields)
        if compacted is fields:
            result[market] = contract
        else:
            changed = True
            result[market] = {**contract, "fields": compacted}
    return result if changed else contracts


def hydrate_row(row: dict) -> dict:
    """Expand a ``choice_list`` reference back to its cached choices without copying them."""
    if not isinstance(row, dict):
        return row
    ref = row.get("choice_list")
    if not isinstance(ref, dict):
        return row
    name = ref.get("name")
    if not isinstance(name, str):
        return row
    source = _sources().get(name)
    if source is None:
        return row
    payload = source[2]()
    if all(key in row for key in payload):
        return row
    hydrated = {key: value for key, value in row.items() if key != "choice_list"}
    hydrated.update(payload)
    return hydrated


def share(form: dict) -> dict:
    """The form with each shared list replaced by its name and version.

    A row whose choices differ from the shared list (a builder narrowed them for
    this listing) keeps its own."""
    return {**form, "fields": compact_fields(str(form.get("marketplace") or ""), form.get("fields") or [])}
