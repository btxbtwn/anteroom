"""Read-only editor definitions from the catalogs used by direct List."""
from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy.orm import Session

from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services import browser_bridge, direct_preflight
from vendoo_studio.services.native_marketplaces import NativeMarketplaceError


async def editor_form(conv_id: str, marketplace: str, revision_id: str | None, db: Session) -> dict:
    """Never start a job or save a draft just to display the form."""
    conv = ConversationRepo(db).get(conv_id)
    if not conv:
        raise NativeMarketplaceError(404, "Conversation not found")
    listing = ListingRepo(db).get_current(conv_id)
    revision = ListingRepo(db).get_revision(listing.current_revision_id) if listing else None
    if not revision:
        raise NativeMarketplaceError(404, "Generate a listing first.")
    if revision_id and revision.id != revision_id:
        raise NativeMarketplaceError(409, "The listing changed. Refresh its category choices.")
    snapshot = direct_preflight.prepare(db, conv, revision.listing_json, marketplace)
    if marketplace == "ebay":
        return await ebay_form(snapshot)
    if marketplace == "sellwild":
        return sellwild_form(snapshot, conv_id, db)
    from vendoo_studio.routes.extension import extension_manager

    if not extension_manager.connected:
        raise NativeMarketplaceError(409, "Connect Chrome to load this marketplace’s current form.")
    categories = []
    if marketplace == "mercari":
        from vendoo_studio.models.catalog import CategoryTreeNode

        categories = [{"label": row.path, "value": row.category_id} for row in db.query(CategoryTreeNode).filter(
            CategoryTreeNode.marketplace == "mercari", CategoryTreeNode.is_leaf.is_(True), CategoryTreeNode.has_children.is_(False),
        ).order_by(CategoryTreeNode.path)]
    try:
        answer = await browser_bridge.request(SimpleNamespace(id=None), "marketplace.form",
                                              {"marketplace": marketplace, "listing": snapshot, "categories": categories}, timeout=35)
    except browser_bridge.BrowserBridgeError as exc:
        raise NativeMarketplaceError(502, "Chrome could not read the marketplace form. Refresh choices.") from exc
    form = answer.get("form") if isinstance(answer, dict) and answer.get("ok") else None
    if not isinstance(form, dict) or form.get("marketplace") != marketplace or form.get("native") is not True:
        message = answer.get("error") if isinstance(answer, dict) else None
        raise NativeMarketplaceError(502, message or "The marketplace did not return its form. Update the Chrome bridge and refresh choices.")
    return form


def field(path: str, label: str, value, **extra) -> dict:
    return {"key": path, "path": path, "label": label, "value": value if value is not None else "",
            "required": False, "multi": False, "selection_only": False, "options": [], **extra}


def base(snapshot: dict, title_max: int) -> list[dict]:
    return [field("title", "Title", snapshot.get("title"), required=True, max_length=title_max),
            field("description", "Description", snapshot.get("description"), required=True),
            field("price", "Price", snapshot.get("price"), required=True, input_type="number")]


def package(snapshot: dict) -> list[dict]:
    return [field(key, label, snapshot.get(key), locked=True, help="Change the package in Item Details.")
            for key, label in (("weight_lb", "Packed weight (lb)"), ("weight_oz", "Packed weight (oz)"),
                               ("package_dimensions_in", "Package dimensions (in)"))]


async def ebay_form(snapshot: dict) -> dict:
    from vendoo_studio.services import marketplace_schemas
    from vendoo_studio.services.ebay_listings import CONDITION_IDS, ebay_aspects, ebay_category_id, ebay_condition

    from vendoo_studio.services.listing_result import ListingProblem

    try:
        category = ebay_category_id(snapshot)
    except ListingProblem:
        category = ""
    try:
        schema = await marketplace_schemas.fields("ebay", category) if category else []
    except Exception as exc:  # noqa: BLE001 - never substitute a different form
        raise NativeMarketplaceError(502, "eBay’s category fields could not be loaded. Check its connection and refresh choices.") from exc
    aspects = ebay_aspects(snapshot, snapshot.get("form") or {})
    fields = base(snapshot, 80)
    fields.append(field("direct_category_ids.ebay", "Category", (snapshot.get("direct_category_ids") or {}).get("ebay_path")
                        or (snapshot.get("marketplace_categories") or {}).get("ebay") or category,
                        help="Changing category reloads its condition and item-specific choices."))
    for row in schema:
        name = row["name"]
        condition = row["id"] == "condition"
        value = CONDITION_IDS[ebay_condition(snapshot)] if condition else aspects.get(name, [])
        if not row["multi"] and isinstance(value, list):
            value = value[0] if value else ""
        values = row.get("values") or []
        inherited = {"size": "Size follows the tag size on General. Change it there.",
                     "department": "Department follows General. Change it there."}
        fields.append(field(f"ebay_specifics.{name}", name, value, required=row["required"], multi=row["multi"],
                            locked=name.casefold() in inherited, **({"help": inherited[name.casefold()]} if name.casefold() in inherited else {}),
                            selection_only=row["mode"] == "selection", options=[v["label"] for v in values],
                            option_values={v["label"]: str(v["id"]) for v in values}, options_source="marketplace",
                            max_values=row.get("max_values"), max_length=row.get("max_length"),
                            value_constraints={v["label"]: v["constraints"] for v in values if v.get("constraints")}))
    details = snapshot.get("ebay_specifics") or {}
    floors = ((snapshot.get("form") or {}).get("specifics", {}).get("pricingFormatDetails") or {}).get("fixedPrice") or {}
    fields.extend([field("quantity", "Quantity", snapshot.get("quantity") or 1, input_type="number"),
                   field("sku", "SKU", snapshot.get("sku"), locked=True, help="SKU comes from Item Details."),
                   field("ebay_specifics.conditionDescription", "Condition description", details.get("conditionDescription"), locked=True,
                         help="Anteroom uses the standard condition note. Describe this item’s flaws in Description."),
                   field("ebay_specifics.bestOffer.autoAcceptPrice", "Automatically accept offers at", floors.get("acceptOffersOfAtLeast"), input_type="number",
                         help="When blank, Anteroom uses the listing price minus $2."),
                   field("ebay_specifics.bestOffer.minimumOfferPrice", "Decline offers below", floors.get("declineOffersLowerThan"), input_type="number",
                         help="When blank, Anteroom uses the listing price minus $4."),
                   *[field(f"ebay_specifics.{key}", key.upper(), details.get(key)) for key in ("upc", "ean", "isbn")],
                   *[field(f"ebay_specifics.{key}", label, details.get(key)) for key, label in
                     (("primaryStoreCategory", "Primary store category"), ("secondaryStoreCategory", "Secondary store category"))],
                   *package(snapshot)])
    return {"marketplace": "ebay", "native": True, "known": True, "category_id": category, "fields": fields,
            "notes": ["Category aspects and condition choices come from eBay. Shipping, payment and return policies come from Settings."]}


def sellwild_form(snapshot: dict, conv_id: str, db: Session) -> dict:
    from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
    from vendoo_studio.services.sellwild_listing import VEHICLE_CATEGORIES, chosen, sellwild_catalog

    catalog = sellwild_catalog()
    picks = chosen(snapshot, MarketplaceListingRepo(db).get(conv_id, "sellwild"))
    fields = base(snapshot, 255)
    for key in ("category", "condition"):
        options = catalog["categories" if key == "category" else "conditions"]
        value = next((row["label"] for row in options if row["id"] == picks[f"{key}_id"]), "Choose one beside List")
        fields.append(field(f"sellwild_specifics.native{key.title()}Id", key.title(), value, locked=True,
                            help="Saved selection in Anteroom. Choose new values beside List."))
    if picks["category_id"] in VEHICLE_CATEGORIES:
        details = snapshot.get("sellwild_specifics") or {}
        fields.extend(field(f"sellwild_specifics.{key}", label, details.get(key), required=True)
                      for key, label in (("vin", "VIN"), ("mileage", "Mileage")))
    else:
        fields.extend(package(snapshot))
    return {"marketplace": "sellwild", "native": True, "known": True, "category_id": str(picks["category_id"] or ""),
            "fields": fields, "notes": ["Sellwild has no category-specific attributes. Category and condition are selected beside List."]}
