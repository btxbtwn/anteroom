"""What a direct List to Poshmark, Mercari, or Depop needs before anything is written.

Send to Vendoo still uses ``marketplace_category_ids`` and Vendoo's field names.
These checks read the same snapshot and only decide whether direct List can start.
A color Poshmark does not list, a price it will round, and a quantity above one
are said beside List. A missing category, an overlong description, a missing
Depop parcel, or a parcel title Depop does not ship holds List off. A size
Poshmark does not offer is added when Chrome has read Poshmark's catalog.
"""
from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP

from vendoo_studio.services.direct_preflight import Context, gap, requirement, seeded_subcategories

# Poshmark's catalog colors (catalog_v2, read 2026-10-25). Grey and Beige are its Gray and Tan.
POSHMARK_COLORS = (
    "Black", "Blue", "Brown", "Cream", "Gold", "Gray", "Green", "Orange", "Pink", "Purple", "Red",
    "Silver", "Yellow", "Tan", "White",
)
_POSHMARK_TRANSLATE = {"grey": "Gray", "beige": "Tan"}


def _money(value) -> Decimal | None:
    try:
        amount = Decimal(str(value).strip())
    except Exception:  # noqa: BLE001 - a blank or odd price is handled by the caller
        return None
    return amount if amount.is_finite() else None


def _whole_dollars(amount: Decimal) -> int:
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def poshmark_color_name(value: str) -> str | None:
    """Poshmark's spelling of a color, or None when that color is left off the listing."""
    text = str(value or "").strip()
    if not text:
        return None
    translated = _POSHMARK_TRANSLATE.get(text.casefold(), text)
    return next((name for name in POSHMARK_COLORS if name.casefold() == translated.casefold()), None)


@requirement("poshmark")
def poshmark_requirements(snapshot: dict, context: Context) -> list[dict]:
    del context
    found: list[dict] = []
    title = str(snapshot.get("title") or "").strip()
    description = str(snapshot.get("description") or "").strip()
    if not title:
        found.append(gap("title", "Poshmark needs a title."))
    elif len(title) > 80:
        found.append(gap("title", "Poshmark titles are at most 80 characters."))
    if not description:
        found.append(gap("description", "Poshmark needs a description."))
    elif len(description) > 1500:
        found.append(gap("description", "Poshmark descriptions are at most 1,500 characters."))
    from vendoo_studio.services.seller_overrides import price_for

    amount = _money(price_for(snapshot, "poshmark"))
    if amount is None or amount <= 0:
        found.append(gap("price", "Poshmark needs a price of $3 or more."))
    else:
        whole = _whole_dollars(amount)
        if amount != whole:
            found.append(gap("price", f"Poshmark lists this at ${whole}.", blocking=False))
        if whole < 3:
            found.append(gap("price", "Poshmark needs a price of $3 or more."))
    dropped = []
    for value in (snapshot.get("primaryColor") or snapshot.get("color"), snapshot.get("secondaryColor")):
        text = str(value or "").strip()
        if text and poshmark_color_name(text) is None and text not in dropped:
            dropped.append(text)
    if dropped:
        listed = " and ".join(dropped)
        found.append(gap("primaryColor", f"Poshmark does not list {listed}, so that color is left off.", blocking=False))
    direct = snapshot.get("direct_category_ids") if isinstance(snapshot.get("direct_category_ids"), dict) else {}
    vendoo = snapshot.get("marketplace_category_ids") if isinstance(snapshot.get("marketplace_category_ids"), dict) else {}
    objects = snapshot.get("marketplace_category_objects") if isinstance(snapshot.get("marketplace_category_objects"), dict) else {}
    if not str(direct.get("poshmark") or "").strip() and not str(vendoo.get("poshmark") or "").strip() \
            and not (isinstance(objects.get("poshmark"), dict) and objects["poshmark"].get("path")):
        found.append(gap("direct_category_ids.poshmark", "Poshmark needs a category from its own list."))
    return found


def _category_id(snapshot: dict, marketplace: str) -> str:
    direct = snapshot.get("direct_category_ids") if isinstance(snapshot.get("direct_category_ids"), dict) else {}
    if str(direct.get(marketplace) or "").strip():
        return str(direct[marketplace]).strip()
    ids = snapshot.get("marketplace_category_ids") if isinstance(snapshot.get("marketplace_category_ids"), dict) else {}
    return str(ids.get(marketplace) or "").strip()


def _mercari_parent(snapshot: dict, context: Context, chosen: str, category: dict) -> list[str] | None:
    """The subcategories under the Mercari category this list would send, or None for a real leaf.

    Vendoo's tree calls some Mercari parents leaves; Mercari lists them with no size.
    """
    paths = snapshot.get("marketplace_categories") if isinstance(snapshot.get("marketplace_categories"), dict) else {}
    path = " > ".join(part.strip() for part in str(paths.get("mercari") or "").split(">") if part.strip())
    children = seeded_subcategories(context.db, "mercari", category_id=chosen) if chosen \
        else seeded_subcategories(context.db, "mercari", path=path)
    if children:
        return children
    if chosen and category.get("hasChildren") is True and str(category.get("id") or "") == chosen:
        return []
    return None


@requirement("mercari")
def mercari_requirements(snapshot: dict, context: Context) -> list[dict]:
    found: list[dict] = []
    description = str(snapshot.get("description") or "").strip()
    if not description:
        found.append(gap("description", "Mercari needs a description."))
    elif len(description) > 1000:
        found.append(gap("description", "Mercari descriptions are at most 1,000 characters."))
    form = snapshot.get("form") if isinstance(snapshot.get("form"), dict) else {}
    category = (form.get("overrides") or {}).get("categoryV2") if isinstance(form.get("overrides"), dict) else {}
    category = category if isinstance(category, dict) else {}
    chosen = _category_id(snapshot, "mercari") or str(category.get("id") or "")
    chosen = chosen if re.fullmatch(r"\d{1,6}", chosen) else ""
    children = _mercari_parent(snapshot, context, chosen, category)
    if children is not None:
        listed = f" ({', '.join(children)})" if children else ""
        found.append(gap("marketplace_category_ids.mercari",
                         f"That Mercari category has subcategories{listed}. Choose the most specific one."))
    elif not chosen:
        found.append(gap("direct_category_ids.mercari", "Mercari needs a category."))
    # Chrome checks the selected leaf and size against Mercari's own master
    # catalog before uploading. Vendoo's mirrored size table is not consulted.
    return found


@requirement("depop")
def depop_requirements(snapshot: dict, context: Context) -> list[dict]:
    found: list[dict] = []
    specifics = snapshot.get("depop_specifics") if isinstance(snapshot.get("depop_specifics"), dict) else {}
    label = str(specifics.get("parcelSize") or specifics.get("parcel_size") or "").strip()
    if not label:
        found.append(gap("depop_specifics.parcelSize", "Depop needs a parcel size."))
    else:
        from vendoo_studio.services.chrome_catalog import KNOWN_PARCELS, parcel_key

        if parcel_key(label) not in KNOWN_PARCELS:
            found.append(gap("depop_specifics.parcelSize", f'Depop does not offer "{label}" as a parcel size.'))
    if not _category_id(snapshot, "depop"):
        found.append(gap("direct_category_ids.depop", "Depop needs a category from its own list."))
    del context
    return found
