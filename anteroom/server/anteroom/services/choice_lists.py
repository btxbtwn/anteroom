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
def payload(name: str) -> dict | None:
    """The cached choices dict for a shared list name, or None for an unknown name."""
    source = _sources().get(name)
    if source is None:
        return None
    return source[2]()


@cache
def choice_list(name: str) -> tuple[bytes, str] | None:
    """The list as JSON and its version, or None for an unknown name."""
    data = payload(name)
    if data is None:
        return None
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    return body, hashlib.sha256(body).hexdigest()[:16]


@cache
def allowed_values(name: str) -> frozenset[str]:
    """Every label and stored value allowed by a shared choice list."""
    data = payload(name)
    if data is None:
        return frozenset()
    allowed = {str(item) for item in data.get("options") or []}
    allowed.update(str(item) for item in (data.get("option_values") or {}).values())
    return frozenset(allowed)


@cache
def _named_sources() -> dict[tuple[str, str], str]:
    return {(marketplace, path): name for name, (marketplace, path, _load) in _sources().items()}


def _strip_identity_option_values(row: dict) -> dict:
    """Omit redundant identity label->label option_values maps while preserving non-identity IDs."""
    option_values = row.get("option_values")
    options = row.get("options")
    if (isinstance(option_values, dict) and option_values and isinstance(options, list)
            and all(isinstance(key, str) and value == key for key, value in option_values.items())
            and set(option_values) <= {str(option) for option in options}):
        return {key: value for key, value in row.items() if key != "option_values"}
    return row


def _compact_row(
    marketplace: str,
    row: dict,
    named: dict[tuple[str, str], str],
    *,
    strip_identity_option_values: bool = True,
) -> dict:
    if not isinstance(row, dict):
        return row
    name = named.get((str(marketplace or ""), str(row.get("path") or row.get("key") or "")))
    if name:
        listed = choice_list(name)
        if listed is not None:
            existing = row.get("choice_list")
            if isinstance(existing, dict) and existing.get("name") == name and "options" not in row and "option_values" not in row:
                if existing.get("version") == listed[1]:
                    return row
                return {**row, "choice_list": {"name": name, "version": listed[1]}}
            data = payload(name)
            if data and all((row.get(key) is value or row.get(key) == value) for key, value in data.items()):
                compacted = {key: value for key, value in row.items() if key not in data}
                compacted["choice_list"] = {"name": name, "version": listed[1]}
                return compacted
    return _strip_identity_option_values(row) if strip_identity_option_values else row


def compact_row(marketplace: str, row: dict, *, strip_identity_option_values: bool = True) -> dict:
    """Replace a row's static shared choice list with its ``choice_list`` reference."""
    return _compact_row(
        marketplace,
        row,
        _named_sources(),
        strip_identity_option_values=strip_identity_option_values,
    )


def compact_fields(
    marketplace: str,
    fields: list[dict],
    *,
    strip_identity_option_values: bool = True,
) -> list[dict]:
    """Return ``fields`` with static shared choice lists replaced by compact references."""
    if not fields:
        return fields
    named = _named_sources()
    rows = []
    changed = False
    for row in fields:
        compacted = _compact_row(
            marketplace,
            row,
            named,
            strip_identity_option_values=strip_identity_option_values,
        )
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
    data = payload(name)
    if data is None:
        return row
    if all(key in row for key in data):
        return row
    hydrated = {key: value for key, value in row.items() if key != "choice_list"}
    hydrated.update(data)
    return hydrated


def share(form: dict) -> dict:
    """The form with each shared list replaced by its name and version.

    A row whose choices differ from the shared list (a builder narrowed them for
    this listing) keeps its own. Non-shared rows stay byte-for-byte identical so
    clients expanding ``choice_list`` references recover ``read_form()`` verbatim.
    """
    return {
        **form,
        "fields": compact_fields(
            str(form.get("marketplace") or ""),
            form.get("fields") or [],
            strip_identity_option_values=False,
        ),
    }
