from __future__ import annotations

from vendoo_studio.services.user_settings import read_settings, update_settings

# Direct marketplace forms that use the shared category-field generation pass.
FILLABLE_MARKETPLACES = ("ebay", "etsy", "poshmark", "mercari", "depop", "sellwild")


# Marketplaces with direct listing connections.
MARKETPLACE_CATALOG: tuple[tuple[str, str, bool], ...] = (
    ("ebay", "eBay", True),
    ("poshmark", "Poshmark", True),
    ("mercari", "Mercari", True),
    ("depop", "Depop", True),
    ("etsy", "Etsy", True),

    ("vinted", "Vinted", False),
    ("sellwild", "Sellwild", True),
)

KNOWN_MARKETPLACES = tuple(item[0] for item in MARKETPLACE_CATALOG)
# New installs start on the original five; the rest are opted into in Settings.
DEFAULT_SELECTED = ["ebay", "etsy", "poshmark", "mercari", "depop"]


def catalog_payload() -> list[dict]:
    return [
        {"id": item_id, "label": label, "fillable": fillable}
        for item_id, label, fillable in MARKETPLACE_CATALOG
    ]


def normalize_selected(raw: object) -> list[str]:
    """Normalize a marketplace selection into catalog order.

    Only marketplaces with a direct listing connection can be selected.
    """
    if not isinstance(raw, list):
        return list(DEFAULT_SELECTED)
    chosen = {str(item).strip().lower() for item in raw if str(item).strip()}
    unknown = sorted(chosen - set(KNOWN_MARKETPLACES))
    if unknown:
        raise ValueError(f"Unknown marketplace: {', '.join(unknown)}")
    return [item_id for item_id in KNOWN_MARKETPLACES if item_id in chosen]


def get_selected_marketplaces() -> list[str]:
    payload = read_settings()
    raw = payload.get("marketplaces")
    if not isinstance(raw, list):
        return list(DEFAULT_SELECTED)
    # A marketplace Anteroom no longer offers (Facebook) drops out; the rest of the choice stays.
    return normalize_selected([item for item in raw if str(item).strip().lower() in KNOWN_MARKETPLACES])


def set_selected_marketplaces(selected: object) -> list[str]:
    normalized = normalize_selected(selected)
    update_settings(lambda payload: payload.__setitem__("marketplaces", normalized))
    return normalized


def selected_fillable_platforms(selected: list[str] | None = None) -> list[str]:
    chosen = set(selected if selected is not None else get_selected_marketplaces())
    return [marketplace for marketplace in FILLABLE_MARKETPLACES if marketplace in chosen]
