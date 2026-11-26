"""Copy Item Details measurements into Depop and apply marketplace edits."""

from __future__ import annotations

import re
from typing import Any

from anteroom.services.general_measurements import active_garment, stored_measurements

# Anteroom key → Depop id and the labels a form row might use.
DEPOP_MEASUREMENT_FIELDS: tuple[dict[str, Any], ...] = (
    {"id": "pit_to_pit", "keys": ("pitToPit",), "labels": ("Pit to pit", "Pit to Pit", "Chest", "Bust")},
    {"id": "length", "keys": ("length",), "labels": ("Length", "Body length")},
    {"id": "sleeve", "keys": ("sleeve",), "labels": ("Sleeve", "Sleeve length")},
    {"id": "waist", "keys": ("waist",), "labels": ("Waist",)},
    {"id": "rise", "keys": ("rise",), "labels": ("Rise",)},
    {"id": "inseam", "keys": ("inseam",), "labels": ("Inseam", "Inside leg")},
    {"id": "leg_opening", "keys": ("legOpening",), "labels": ("Leg opening", "Hem")},
    {"id": "hip", "keys": ("hip",), "labels": ("Hip", "Hips")},
)
_PATH = "depop_specifics.measurements"


def depop_measurements_from_notes(notes: dict | None, listing: dict | None = None) -> dict[str, Any]:
    """Depop's measurements block, in inches, from Item Details."""
    parsed = notes if isinstance(notes, dict) else {}
    values = stored_measurements(parsed)[active_garment(listing, parsed)]
    rows = []
    for field in DEPOP_MEASUREMENT_FIELDS:
        amount = next((values.get(key) for key in field["keys"] if values.get(key)), "")
        if not amount:
            continue
        rows.append({
            "id": field["id"],
            "label": field["labels"][0],
            "value": amount,
            "unit": "in",
        })
    return {"unit": "in", "values": rows}


def measurement_inputs(specifics: dict) -> dict:
    stored = specifics.get("measurements")
    block = stored if isinstance(stored, dict) else {}
    aliases = {"pit_to_pit": "pit-to-pit", "sleeve": "sleeve-length", "leg_opening": "leg-opening", "hip": "hips"}
    result = {}
    rows = block.get("values")
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not row.get("id") or row.get("value") is None:
            continue
        value = str(row["value"]).strip()
        if not re.search(r'(?:cm|inches|inch|in\.?|["″])$', value, re.I):
            value = f"{value} {row.get('unit') or block.get('unit') or 'in'}"
        result[aliases.get(row["id"], row["id"])] = value
    overrides = specifics.get("measurementInputs")
    if isinstance(overrides, dict):
        result.update(overrides)
    return result


def sync_depop_measurements(
    listing: dict,
    notes: dict | None,
    *,
    force: bool = False,
    clear_empty: bool = False,
) -> bool:
    """Copy stored inches onto the Depop block unless the seller locked it."""
    from anteroom.services.seller_overrides import is_locked

    if not isinstance(listing, dict):
        return False
    if not force and is_locked(listing, _PATH):
        return False
    built = depop_measurements_from_notes(notes, listing)
    if not built["values"] and not force and not clear_empty:
        return False
    depop = listing.get("depop_specifics")
    if not isinstance(depop, dict):
        depop = {}
    if built["values"]:
        if depop.get("measurements") == built:
            return False
        depop = dict(depop)
        depop["measurements"] = built
    else:
        if "measurements" not in depop:
            return False
        depop = dict(depop)
        depop.pop("measurements", None)
    listing["depop_specifics"] = depop
    return True
