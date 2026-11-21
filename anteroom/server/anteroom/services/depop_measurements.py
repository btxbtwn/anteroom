"""Map Anteroom's stored garment measurements into Depop's measurements block.

Depop's live "add measurements" field ids are not in this repo or its fixtures.
The ids and labels below are the ones the extension sends and the form filler
matches by label. Confirm them against a live Depop listing before relying on
the draft body.
"""

from __future__ import annotations

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
