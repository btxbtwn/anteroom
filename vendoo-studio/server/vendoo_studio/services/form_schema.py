"""The fields a marketplace form shows for one item.

The category schema is that form. Department picks which size scale is offered.
Condition hides a field that names a different condition. A saved value the
form does not show stays on the listing.
"""

from __future__ import annotations

import re
from typing import Any

_BUCKET_RE = {
    "women": re.compile(r"\b(women|woman|ladies|womens)\b", re.I),
    "men": re.compile(r"\b(men|man|mens)\b", re.I),
    "girls": re.compile(r"\bgirls?\b", re.I),
    "boys": re.compile(r"\bboys?\b", re.I),
    "kids": re.compile(r"\b(kids?|baby|infant|toddler)\b", re.I),
}
_COMPATIBLE = {
    "women": {"women"},
    "men": {"men"},
    "girls": {"girls", "kids"},
    "boys": {"boys", "kids"},
    "kids": {"kids", "girls", "boys"},
}


def department_text(listing: dict | None) -> str:
    if not isinstance(listing, dict):
        return ""
    ebay = listing.get("ebay_specifics")
    if not isinstance(ebay, dict):
        ebay = {}
    return " ".join(str(part or "") for part in (
        listing.get("department"), ebay.get("department"), listing.get("category_path"), listing.get("title"),
    ))


def department_bucket(listing: dict | None) -> str:
    """women, men, girls, boys, kids, or empty when the item does not say."""
    text = department_text(listing)
    if _BUCKET_RE["girls"].search(text) and not _BUCKET_RE["women"].search(text):
        return "girls"
    if _BUCKET_RE["women"].search(text):
        return "women"
    if _BUCKET_RE["boys"].search(text):
        return "boys"
    if _BUCKET_RE["kids"].search(text):
        return "kids"
    if _BUCKET_RE["men"].search(text):
        return "men"
    return ""


def condition_text(listing: dict | None) -> str:
    if not isinstance(listing, dict):
        return ""
    ebay = listing.get("ebay_specifics")
    if not isinstance(ebay, dict):
        ebay = {}
    return " ".join(str(part or "") for part in (listing.get("condition"), ebay.get("condition")))


def field_applies(label: str, listing: dict | None) -> bool:
    """Whether this category field belongs on the form for this department and condition."""
    named = {bucket for bucket, pattern in _BUCKET_RE.items() if pattern.search(label or "")}
    bucket = department_bucket(listing)
    if bucket and named and named.isdisjoint(_COMPATIBLE[bucket]):
        return False
    cond = condition_text(listing).lower()
    text = str(label or "").lower()
    if not cond.strip():
        return True
    preowned = bool(re.search(r"pre-?owned|\bused\b|worn", cond))
    fresh = bool(re.search(r"\bnew\b|\bnwt\b", cond)) and not preowned
    if preowned and re.search(r"new with tags|nwt only|brand new", text):
        return False
    if fresh and re.search(r"pre-?owned|used condition", text):
        return False
    return True


def live_belongs_on_form(live: dict | None) -> bool:
    """eBay required and recommended aspects are on the form. Optional ones wait for Show all."""
    usage = str((live or {}).get("usage") or "").upper()
    return usage != "OPTIONAL"


def labels_for_spec(spec: Any, listing: dict | None) -> list[str]:
    """The choices this item's department is offered. A size scale for another department is left out."""
    scales = getattr(spec, "scales", None) or {}
    if scales:
        from vendoo_studio.services.vendoo_specifics import _scales_for_department

        ordered = _scales_for_department(scales, department_text(listing))
        bucket = department_bucket(listing)
        named = [item for item in ordered if _scale_matches(item, bucket)]
        labels: list[str] = []
        for _, scale in (named or ordered):
            for label in (scale.get("options") or {}).values():
                text = str(label)
                if text and text not in labels:
                    labels.append(text)
        return labels
    return sorted(str(label) for label in (getattr(spec, "options", None) or {}).values())


def _scale_matches(item: tuple[str, dict], bucket: str) -> bool:
    if not bucket:
        return False
    scale_id, scale = item
    return not {bucket}.isdisjoint(
        {name for name, pattern in _BUCKET_RE.items() if pattern.search(f"{scale_id} {scale.get('display') or ''}")}
        & _COMPATIBLE[bucket]
    )


def form_schema_prompt(listing: dict | None, *, exclude=()) -> str:
    """The same fields the form shows, for chat. Cached schemas only; nothing is fetched."""
    if not isinstance(listing, dict):
        return ""
    from vendoo_studio.services.category_fields import listing_category_ids, load_fields, load_rows

    lines: list[str] = []
    for marketplace, category_id in listing_category_ids(listing).items():
        if marketplace in exclude:
            continue
        rows = _cached_form_rows(marketplace, category_id, listing, load_fields, load_rows)
        if not rows:
            continue
        lines.append(marketplace)
        lines.extend(rows)
    if not lines:
        return ""
    return (
        "\n\n--- Marketplace form for this item ---\n"
        "These are the fields that marketplace's form shows for this category, department, and condition. "
        "Use these names and these choices. Other saved values stay on the listing; do not clear them.\n"
        + "\n".join(lines)
    )


def _cached_form_rows(marketplace: str, category_id: str, listing: dict, load_fields, load_rows) -> list[str]:
    live_key = {"ebay": "ebay_aspects", "etsy": "etsy_properties"}.get(marketplace)
    live = load_rows(live_key, category_id) if live_key else None
    if live:
        lines = []
        for row in live:
            # eBay's condition list stays on the form's own Condition row.
            if not isinstance(row, dict) or str(row.get("id") or "") == "condition" or not live_belongs_on_form(row):
                continue
            name = str(row.get("name") or "").strip()
            if not name or not field_applies(name, listing):
                continue
            options = [str(value.get("label") or "") for value in row.get("values") or [] if isinstance(value, dict) and value.get("label")]
            line = f"- {name}" + (" (required)" if row.get("required") else "")
            if options:
                shown = ", ".join(options[:12])
                line += f" — {shown}" + ("…" if len(options) > 12 else "")
            lines.append(line)
        return lines
    specs = load_fields(marketplace, category_id) or {}
    lines = []
    for spec in specs.values():
        if not field_applies(spec.display, listing):
            continue
        options = labels_for_spec(spec, listing)
        line = f"- {spec.display}" + (" (required)" if spec.required else "")
        if options:
            shown = ", ".join(options[:12])
            line += f" — {shown}" + ("…" if len(options) > 12 else "")
        lines.append(line)
    return lines
