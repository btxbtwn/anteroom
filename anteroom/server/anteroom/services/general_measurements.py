"""General form's Measurements (in) fields: schema for chat and writes to Conversation.notes."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from anteroom.services.vendoo_import import parse_notes

# Keys and labels mirror GARMENTS in src/components/garmentMeasurements.ts,
# SellerInputs.swift, and GARMENT_MEASUREMENTS in listing_generate.py.
GARMENT_SPECS: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    ("top", "Top", (("pitToPit", "Pit to Pit"), ("length", "Length"), ("sleeve", "Sleeve"))),
    (
        "pants",
        "Pants",
        (("waist", "Waist"), ("rise", "Rise"), ("inseam", "Inseam"), ("legOpening", "Leg Opening")),
    ),
    ("skirt", "Skirt", (("waist", "Waist"), ("hip", "Hip"), ("length", "Length"))),
)

_GARMENT_IDS = frozenset(garment for garment, _, _ in GARMENT_SPECS)
_GARMENT_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    garment: fields for garment, _, fields in GARMENT_SPECS
}
_PANTS_WORDS = re.compile(
    r"\b(pants|jeans|shorts|trousers|slacks|chinos|cargos?|joggers|sweatpants|leggings|overalls)\b",
    re.I,
)
_SKIRT_WORDS = re.compile(r"\b(skirts?|skorts?)\b", re.I)
_MIXED_FRACTION_RE = re.compile(r"^(\d+)\s*[- ]\s*(\d+)\s*/\s*(\d+)$")
_FRACTION_RE = re.compile(r"^(\d+)\s*/\s*(\d+)$")
_UNIT_SUFFIX_RE = re.compile(r'(?:\s*(?:inches|inch|in\.?|["”″]|\'\'))+\s*$', re.I)


def _fold(text: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").casefold())


_CANONICAL_PATHS: dict[str, str] = {
    "measurements.garment": "measurements.garment",
    "measurements.other": "measurements.other",
    "measurements.descriptionmeasurements": "measurements.other",
}
for _garment, _, _fields in GARMENT_SPECS:
    for _key, _label in _fields:
        _canonical = f"measurements.{_garment}.{_key}"
        _CANONICAL_PATHS[f"measurements.{_garment}.{_fold(_key)}"] = _canonical
        _CANONICAL_PATHS[f"measurements.{_garment}.{_fold(_label)}"] = _canonical


def canonical_measurement_path(path: object) -> str | None:
    """Return the canonical General measurement path for a dotted path or JSON Pointer."""
    raw = str(path or "").strip()
    if not raw:
        return None
    if raw.startswith("/"):
        parts = [part.replace("~1", "/").replace("~0", "~") for part in raw.lstrip("/").split("/") if part]
    else:
        parts = [part for part in raw.split(".") if part]
    if not parts or parts[0].casefold() != "measurements":
        return None
    if len(parts) == 2:
        return _CANONICAL_PATHS.get(f"measurements.{_fold(parts[1])}")
    if len(parts) == 3:
        garment = normalize_garment(parts[1])
        if not garment:
            return None
        return _CANONICAL_PATHS.get(f"measurements.{garment}.{_fold(parts[2])}")
    return None


def is_measurement_path(path: object) -> bool:
    return canonical_measurement_path(path) is not None


def is_garment_measurement_path(path: object) -> bool:
    canonical = canonical_measurement_path(path)
    return canonical is not None and canonical != "measurements.other"


def normalize_garment(value: object) -> str | None:
    folded = _fold(value)
    if folded == "shorts":
        return "pants"
    if folded in _GARMENT_IDS:
        return folded
    return None


def _stored_measurements(notes: dict[str, Any]) -> dict[str, dict[str, str]]:
    source = notes.get("measurements")
    raw = source if isinstance(source, dict) else {}
    out: dict[str, dict[str, str]] = {"top": {}, "pants": {}, "skirt": {}}
    for garment, _, fields in GARMENT_SPECS:
        group = raw.get(garment)
        if group is None and garment == "pants":
            group = raw.get("shorts")
        if not isinstance(group, dict):
            continue
        for key, _label in fields:
            val = group.get(key)
            if isinstance(val, (str, int, float)) and not isinstance(val, bool):
                text = str(val).strip()
                if text:
                    out[garment][key] = text
    return out


def _guess_garment(listing: dict[str, Any] | None) -> str:
    if not isinstance(listing, dict):
        return "top"
    category = str(listing.get("category_path") or "").strip()
    hint = category or str(listing.get("title") or "")
    if _SKIRT_WORDS.search(hint):
        return "skirt"
    return "pants" if _PANTS_WORDS.search(hint) else "top"


def active_garment(listing: dict[str, Any] | None, notes: dict[str, Any]) -> str:
    """Mirror itemDetailsData.ts chooseGarment so chat sees the same active tab as the editor."""
    saved = normalize_garment(notes.get("garment"))
    values = _stored_measurements(notes)
    if saved and (notes.get("garmentExplicit") is True or saved != "top" or any(values.values())):
        return saved
    return next((garment for garment, _, _ in GARMENT_SPECS if values[garment]), None) or _guess_garment(listing)


def measurement_form_fields(listing: dict[str, Any] | None, raw_notes: str | None) -> list[dict[str, Any]]:
    """General form's Measurements (in) rows, backed by Conversation.notes."""
    notes = parse_notes(raw_notes)
    garment = active_garment(listing, notes)
    values = _stored_measurements(notes)
    rows: list[dict[str, Any]] = [
        {
            "key": "measurements.garment",
            "path": "measurements.garment",
            "label": "Garment type",
            "section": "Measurements (in)",
            "options": [label for _, label, _ in GARMENT_SPECS],
            "option_values": {label: garment_id for garment_id, label, _ in GARMENT_SPECS},
            "selection_only": True,
            "value": garment,
            "help": (
                "Selects which General Measurements (in) inputs are shown (Top, Pants, or Skirt). "
                "Save inch numbers in measurements.<garment>.<field> and extra notes in measurements.other."
            ),
        },
    ]
    for garment_id, garment_label, fields in GARMENT_SPECS:
        for field_key, field_label in fields:
            path = f"measurements.{garment_id}.{field_key}"
            rows.append({
                "key": path,
                "path": path,
                "label": field_label,
                "section": "Measurements (in)",
                "group": garment_label,
                "input_type": "number",
                "suffix": "in",
                "value": values[garment_id].get(field_key, ""),
            })
    rows.append({
        "key": "measurements.other",
        "path": "measurements.other",
        "label": "Other measurements",
        "section": "Measurements (in)",
        "placeholder": 'Anything the fields above miss, e.g. Hem 21"',
        "value": str(notes.get("descriptionMeasurements") or ""),
    })
    return rows


def _format_decimal(amount: Decimal) -> str:
    if not amount.is_finite() or amount < 0:
        return ""
    normalized = amount.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def normalize_measurement_number(value: Any) -> str:
    """Format an inch measurement as a plain numeric string for ItemDetails and SellerInputs."""
    if value is None or isinstance(value, bool) or isinstance(value, (dict, list)):
        return ""
    if isinstance(value, (int, float, Decimal)):
        try:
            return _format_decimal(Decimal(str(value)))
        except InvalidOperation:
            return ""
    text = _UNIT_SUFFIX_RE.sub("", str(value).strip()).strip()
    if not text:
        return ""
    mixed = _MIXED_FRACTION_RE.fullmatch(text)
    if mixed:
        whole, num, den = int(mixed.group(1)), int(mixed.group(2)), int(mixed.group(3))
        if den == 0:
            return ""
        return _format_decimal(Decimal(whole) + (Decimal(num) / Decimal(den)))
    frac = _FRACTION_RE.fullmatch(text)
    if frac:
        num, den = int(frac.group(1)), int(frac.group(2))
        if den == 0:
            return ""
        return _format_decimal(Decimal(num) / Decimal(den))
    try:
        return _format_decimal(Decimal(text))
    except InvalidOperation:
        return ""


def note_changes_for_measurement_updates(
    updates: list[tuple[str, Any, bool]],
    existing_notes: str | None,
    listing: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Return note changes and the final normalized values for accepted paths."""
    notes = parse_notes(existing_notes)
    raw_measurements = notes.get("measurements")
    existing_measurements = raw_measurements if isinstance(raw_measurements, dict) else {}
    changes: dict[str, Any] = {}
    measurement_changes: dict[str, dict[str, str]] = {}
    applied: dict[str, str] = {}
    nonempty_garments: set[str] = set()

    for path, value, is_clear in updates:
        if path == "measurements.garment":
            garment = "" if is_clear else normalize_garment(value)
            if garment is not None:
                applied[path] = garment
            continue
        if path == "measurements.other":
            applied[path] = changes["descriptionMeasurements"] = "" if is_clear else str(value or "").strip()
            continue
        parts = path.split(".")
        if len(parts) != 3 or parts[0] != "measurements":
            continue
        garment, field_key = parts[1], parts[2]
        if garment not in _GARMENT_FIELDS or field_key not in {key for key, _ in _GARMENT_FIELDS[garment]}:
            continue
        cleaned = "" if is_clear else normalize_measurement_number(value)
        if not is_clear and not cleaned:
            continue
        bucket = measurement_changes.setdefault(garment, {})
        if (
            garment == "pants"
            and not bucket
            and "pants" not in existing_measurements
            and isinstance(existing_measurements.get("shorts"), dict)
        ):
            bucket.update(_stored_measurements(notes)["pants"])
        bucket[field_key] = applied[path] = cleaned
        if cleaned:
            nonempty_garments.add(garment)

    if measurement_changes:
        changes["measurements"] = measurement_changes
    if "measurements.garment" in applied:
        changes["garment"] = applied["measurements.garment"]
        changes["garmentExplicit"] = bool(changes["garment"])
    elif len(nonempty_garments) == 1:
        changes["garment"] = next(iter(nonempty_garments))
    elif measurement_changes and normalize_garment(notes.get("garment")) is None:
        changes["garment"] = active_garment(listing, notes)
    return changes, applied


_UNAMBIGUOUS_MISSING_FIELD_LABELS: dict[str, str] = {
    "garment": "measurements.garment",
    "garmenttype": "measurements.garment",
    "othermeasurements": "measurements.other",
    "pittopit": "measurements.top.pitToPit",
    "sleeve": "measurements.top.sleeve",
    "rise": "measurements.pants.rise",
    "inseam": "measurements.pants.inseam",
    "legopening": "measurements.pants.legOpening",
    "hip": "measurements.skirt.hip",
}


def missing_field_measurement_path(
    marketplace: str,
    field_label: str,
    listing: dict[str, Any] | None,
    existing_notes: str | None,
) -> str | None:
    """Resolve a General missing_fields row to a measurement path without shadowing package Length."""
    if str(marketplace or "general").strip().lower() not in {"", "general", "unknown"}:
        return None
    direct = canonical_measurement_path(field_label)
    if direct:
        return direct
    folded = _fold(field_label)
    if folded in _UNAMBIGUOUS_MISSING_FIELD_LABELS:
        return _UNAMBIGUOUS_MISSING_FIELD_LABELS[folded]
    if folded == "waist":
        garment = active_garment(listing, parse_notes(existing_notes))
        return f"measurements.{garment}.waist" if garment in {"pants", "skirt"} else "measurements.pants.waist"
    return None
