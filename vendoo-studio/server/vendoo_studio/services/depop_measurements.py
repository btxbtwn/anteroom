"""Map Anteroom's stored garment measurements into Depop's measurements block.

Depop's live "add measurements" field ids are not in this repo or its fixtures.
The ids and labels below are the ones the extension sends and the form filler
matches by label. Confirm them against a live Depop listing before relying on
the draft body.
"""

from __future__ import annotations

from typing import Any

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
_GARMENTS = {"top", "pants", "skirt"}
_PATH = "depop_specifics.measurements"


def _notes(notes: dict | None) -> dict:
    return notes if isinstance(notes, dict) else {}


def garment_measurements(notes: dict | None) -> tuple[str, dict[str, str]]:
    """The selected garment and the inches the seller stored for it."""
    parsed = _notes(notes)
    garment = parsed.get("garment")
    if garment == "shorts":
        garment = "pants"
    if garment not in _GARMENTS:
        return "", {}
    raw = parsed.get("measurements")
    values = raw.get(garment) if isinstance(raw, dict) else None
    if values is None and garment == "pants" and isinstance(raw, dict):
        values = raw.get("shorts")
    if not isinstance(values, dict):
        return garment, {}
    cleaned = {
        str(key): str(value).strip()
        for key, value in values.items()
        if str(value).strip()
    }
    return garment, cleaned


def depop_measurements_from_notes(notes: dict | None) -> dict[str, Any]:
    """Depop's measurements block, in inches, from Item Details."""
    _garment, values = garment_measurements(notes)
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


def sync_depop_measurements(listing: dict, notes: dict | None, *, force: bool = False) -> bool:
    """Copy stored inches onto the Depop block unless the seller locked it."""
    from vendoo_studio.services.seller_overrides import is_locked

    if not isinstance(listing, dict):
        return False
    if not force and is_locked(listing, _PATH):
        return False
    built = depop_measurements_from_notes(notes)
    if not built["values"] and not force:
        return False
    depop = listing.get("depop_specifics")
    if not isinstance(depop, dict):
        depop = {}
    if depop.get("measurements") == built:
        return False
    depop = dict(depop)
    if built["values"]:
        depop["measurements"] = built
    else:
        depop.pop("measurements", None)
    listing["depop_specifics"] = depop
    return True
