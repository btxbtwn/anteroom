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
    try:
        answer = await browser_bridge.request(SimpleNamespace(id=None), "marketplace.form",
                                              {"marketplace": marketplace, "listing": snapshot}, timeout=35)
    except browser_bridge.BrowserBridgeError as exc:
        raise NativeMarketplaceError(502, "Chrome could not read the marketplace form. Reopen the listing to try again.") from exc
    form = answer.get("form") if isinstance(answer, dict) and answer.get("ok") else None
    if not isinstance(form, dict) or form.get("marketplace") != marketplace or form.get("native") is not True:
        message = answer.get("error") if isinstance(answer, dict) else None
        raise NativeMarketplaceError(502, message or "The marketplace did not return its form. Update the Chrome bridge and refresh choices.")
    return form


def field(path: str, label: str, value, **extra) -> dict:
    return {"key": path, "path": path, "label": label, "value": value if value is not None else "",
            "required": False, "multi": False, "selection_only": False, "options": [], **extra}


def _shared(snapshot: dict, marketplace: str, name: str):
    """The form's own title, description, or brand, or General when this form has not set one."""
    from vendoo_studio.services.seller_overrides import followed_entry

    entry = followed_entry(snapshot, marketplace, name)
    if entry is not None:
        return entry[1] if entry[1] is not None else ""
    return snapshot.get(name)


def package(snapshot: dict) -> list[dict]:
    """eBay's "Package weight" in lbs. and oz. side by side, then "Package dimensions", from Item Details."""
    return [field(key, label, snapshot.get(key), locked=True, help="Change the package in Item Details.",
                  **({"width": "half"} if key.startswith("weight_") else {}))
            for key, label in (("weight_lb", "Package weight (lbs.)"), ("weight_oz", "Package weight (oz.)"),
                               ("package_dimensions_in", "Package dimensions (in.)"))]


# eBay's Create listing page (ebay.com/lstng, read 2026-10-27), section by section.
EBAY_SPECIFIC_SECTIONS = {"required": "Item specifics: Required", "recommended": "Item specifics: Additional (optional)",
                          "optional": "Item specifics: Additional (optional)"}


def ebay_specifics_order(schema: list[dict]) -> list[dict]:
    """Item specifics as eBay's page lists them: required, then recommended, then the rest
    (behind "Show more"), each in eBay's order. Personalization Instructions follows Personalize."""
    aspects = [row for row in schema if row.get("id") != "condition"]

    def tier(row: dict) -> int:
        return 0 if row.get("required") else 1 if row.get("usage") == "RECOMMENDED" else 2

    ordered = [row for rank in (0, 1, 2) for row in aspects if tier(row) == rank]
    instructions = next((row for row in ordered if row["name"] == "Personalization Instructions"), None)
    personalize = next((row for row in ordered if row["name"] == "Personalize"), None)
    if instructions and personalize and tier(instructions) == tier(personalize):
        ordered.remove(instructions)
        ordered.insert(ordered.index(personalize) + 1, instructions)
    return ordered


async def ebay_form(snapshot: dict) -> dict:
    from vendoo_studio.services import marketplace_schemas
    from vendoo_studio.services.ebay_listings import CONDITION_IDS, best_offer_terms, condition_description, ebay_aspects, ebay_category_id, ebay_condition, seller_sku
    from vendoo_studio.services.seller_overrides import price_for

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
    details = snapshot.get("ebay_specifics") or {}

    def aspect_field(row: dict, section: str) -> dict:
        name = row["name"]
        condition = row["id"] == "condition"
        value = CONDITION_IDS[ebay_condition(snapshot)] if condition else aspects.get(name, [])
        if not row["multi"] and isinstance(value, list):
            value = value[0] if value else ""
        values = row.get("values") or []
        constraints = marketplace_schemas.constraints_by_label(values)
        # eBay's page offers only its listed sizes, though its API takes others.
        selection = row["mode"] == "selection" or (name == "Size" and bool(values))
        extra = {"help": "Personalize is required before providing Personalization Instructions"} \
            if name == "Personalization Instructions" else {}
        return field(f"ebay_specifics.{name}", "Item condition" if condition else name, value, required=row["required"],
                     multi=row["multi"], section=section, **extra,
                     **({"category_specific": True} if not condition else {}),
                     selection_only=selection,
                     options=list(dict.fromkeys(v["label"] for v in values)),
                     option_values={v["label"]: str(v["id"]) for v in values}, options_source="marketplace",
                     max_values=row.get("max_values"), max_length=row.get("max_length"),
                     **({"value_constraints": constraints} if constraints else {}))

    terms = best_offer_terms(snapshot.get("form") or {}, str(price_for(snapshot, "ebay") or 0), snapshot)
    from vendoo_studio.services import ebay_variations

    varied = ebay_variations.has_variations(snapshot)
    # Auction prices, offers and variations all travel with the category. The
    # editor shows the ones the current format uses, so a format change does
    # not load the form again.
    selling = await _ebay_format_fields(snapshot, category, details)
    variations = await _ebay_variations_field(snapshot, category, schema)
    condition_row = next((row for row in schema if row.get("id") == "condition"), None)
    quantity = (field("quantity", "Quantity", ebay_variations.total_quantity(snapshot), input_type="number", locked=True,
                      help="The quantity is the total of the variations.") if varied else
                field("quantity", "Quantity", snapshot.get("quantity") or 1, input_type="number"))
    fields = [
        field("ebay_specifics.title", "Item title", _shared(snapshot, "ebay", "title"), required=True, max_length=80, section="Title",
              help="Provide a title for your item. Use words people would search for when looking for your item."),
        field("sku", "Custom label (SKU)", seller_sku(snapshot), locked=True, section="Title", max_length=50,
              help="Create a custom label to enter information you want to track, such as your own SKU number. "
                   "It comes from Item Details."),
        field("direct_category_ids.ebay", "Category", (snapshot.get("direct_category_ids") or {}).get("ebay_path")
              or (snapshot.get("marketplace_categories") or {}).get("ebay") or category, section="Item category"),
        field("ebay_specifics.primaryStoreCategory", "First category", details.get("primaryStoreCategory"), section="Item category",
              width="half", help="If you have an eBay Store, select the Store categories of the items you're listing."),
        field("ebay_specifics.secondaryStoreCategory", "Second category", details.get("secondaryStoreCategory"),
              section="Item category", width="half"),
        *[{**row, "section": "Variations"} for row in variations],
        *[aspect_field(row, EBAY_SPECIFIC_SECTIONS["required" if row.get("required") else
                                                    "recommended" if row.get("usage") == "RECOMMENDED" else "optional"])
          for row in ebay_specifics_order(schema)],
        field("ebay_specifics.upc", "UPC", details.get("upc"), max_length=20, section=EBAY_SPECIFIC_SECTIONS["optional"]),
        *([aspect_field(condition_row, "Condition")] if condition_row else []),
        field("ebay_specifics.conditionDescription", "Condition description", condition_description(snapshot, snapshot.get("form") or {}),
              input_type="textarea", max_length=1000, section="Condition",
              help="Let the buyer know what makes your item unique, including any interesting features or flaws."),
        field("ebay_specifics.description", "Description", _shared(snapshot, "ebay", "description"), required=True, section="Description"),
        *[{**row, "section": "Pricing"} for row in selling],
        field("price", "Item price", snapshot.get("price"), required=True, input_type="number", section="Pricing"),
        {**quantity, "section": "Pricing"},
        field("ebay_specifics.bestOffer.enabled", "Allow offers (optional)", terms.get("bestOfferEnabled", True), input_type="boolean",
              section="Pricing", help="Interested buyers can send an offer for this item. You can accept, counter, or decline."),
        field("ebay_specifics.bestOffer.minimumOfferPrice", "Minimum offer", (terms.get("autoDeclinePrice") or {}).get("value", ""),
              input_type="number", section="Pricing", width="half"),
        field("ebay_specifics.bestOffer.autoAcceptPrice", "Auto accept", (terms.get("autoAcceptPrice") or {}).get("value", ""),
              input_type="number", section="Pricing", width="half"),
        *[{**row, "section": "Shipping"} for row in package(snapshot)],
    ]
    return {"marketplace": "ebay", "native": True, "known": True, "category_id": category, "fields": fields}


async def _ebay_variations_field(snapshot: dict, category: str, schema: list[dict]) -> list[dict]:
    """Variations: what they differ by (aspects eBay enables for variations in this leaf) and one row each.

    Included whenever this category allows them. The editor hides the row during
    an auction that has no variation rows yet.
    """
    from vendoo_studio.services import ebay_variations, marketplace_schemas
    from vendoo_studio.services.ebay_client import EbayClient

    varied = ebay_variations.has_variations(snapshot)
    value = ebay_variations.raw(snapshot) if varied else {"aspects": [], "rows": [], "photos": {}}
    if not category:
        return []
    supported: bool | None = None
    try:
        supported = await marketplace_schemas.ebay_variations_supported(await EbayClient.connect(), category)
    except Exception:  # noqa: BLE001 - List checks it again before anything is written
        supported = None
    aspects = [{"name": row["name"], "selection_only": row["mode"] == "selection",
                "options": list(dict.fromkeys(entry["label"] for entry in row.get("values") or []))}
               for row in schema if row.get("variations") and row.get("id") != "condition"]
    if supported is False and not varied:
        return []
    help_text = ("Each variation has its own quantity, SKU and price. eBay lists them as one fixed-price listing "
                 "without offers.")
    if supported is False:
        help_text = "eBay does not take variations in this category. Remove them to list it."
    elif supported is None:
        help_text += " eBay's variation rules for this category could not be loaded; List checks them again."
    return [field(ebay_variations.FIELD, "Variations", value, input_type="variations", variation_aspects=aspects,
                  category_specific=True, help=help_text)]


_FORMAT_CLEARS = ["ebay_specifics.listingDuration", "ebay_specifics.auctionStartPrice",
                  "ebay_specifics.auctionReservePrice", "ebay_specifics.auctionBuyItNow"]


async def _ebay_format_fields(snapshot: dict, category: str, details: dict) -> list[dict]:
    """Format, duration and auction prices for this category.

    When the category allows an auction, the payload carries fixed-price and
    auction rows together. The editor locks duration and hides the auction
    prices while the listing is fixed price.
    """
    from vendoo_studio.services import marketplace_schemas
    from vendoo_studio.services.ebay_client import EbayClient
    from vendoo_studio.services.ebay_listings import (AUCTION, DEFAULT_AUCTION_DURATION, FIXED_PRICE, FORMAT_LABELS,
                                                      duration_label)

    kind = marketplace_schemas._ebay_listing_type(details.get("listingFormat")) or FIXED_PRICE
    allowed: dict[str, list[str]] = {}
    note = ""
    if category:
        try:
            allowed = await marketplace_schemas.ebay_listing_types(await EbayClient.connect(), category)
        except Exception:  # noqa: BLE001 - fixed price needs no lookup; List checks auctions again
            note = "eBay's auction rules for this category could not be loaded. Reopen the listing to try again."
    auction_lengths = allowed.get(AUCTION) or []
    offers_auction = bool(auction_lengths) or kind == AUCTION
    # eBay's Format menu lists Auction, then Buy It Now.
    kinds = ([AUCTION] if offers_auction else []) + [FIXED_PRICE]
    rows = [field("ebay_specifics.listingFormat", "Format", kind, selection_only=True, required=True,
                  options=[FORMAT_LABELS[value] for value in kinds],
                  option_values={FORMAT_LABELS[value]: value for value in kinds}, options_source="marketplace",
                  clears=_FORMAT_CLEARS, **({"help": note} if note else {}))]
    if not offers_auction:
        # Fixed price runs Good 'Til Cancelled; eBay's page shows no duration for it.
        return rows
    lengths = list(dict.fromkeys(["GTC", *auction_lengths]))
    chosen = "GTC" if kind != AUCTION else marketplace_schemas.ebay_duration(details.get("listingDuration")) or (
        DEFAULT_AUCTION_DURATION if DEFAULT_AUCTION_DURATION in auction_lengths or not auction_lengths else auction_lengths[0])
    # eBay's page during an auction: duration, then Starting bid beside Buy It Now, then the reserve.
    rows += [
        field("ebay_specifics.listingDuration", "Auction duration", chosen, selection_only=True, required=True,
              options=[duration_label(value) for value in lengths],
              option_values={duration_label(value): value for value in lengths}, options_source="marketplace"),
        field("ebay_specifics.auctionStartPrice", "Starting bid", details.get("auctionStartPrice"), input_type="number", required=True,
              width="half"),
        field("ebay_specifics.auctionBuyItNow", "Buy It Now (optional)", details.get("auctionBuyItNow"), input_type="number",
              width="half", help="Minimum: 30% more than starting bid"),
        field("ebay_specifics.auctionReservePrice", "Reserve price (optional) — fees apply", details.get("auctionReservePrice"),
              input_type="number", width="half"),
    ]
    return rows


def sellwild_form(snapshot: dict, conv_id: str, db: Session) -> dict:
    """Sellwild's sell page (sellwild.com/sell, read 2026-10-27), section by section and in its order."""
    from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo
    from vendoo_studio.services.sellwild_listing import MAX_PRICE, VEHICLE_CATEGORIES, chosen, package_pounds, package_sides, sellwild_catalog

    catalog = sellwild_catalog()
    picks = chosen(snapshot, MarketplaceListingRepo(db).get(conv_id, "sellwild"))
    details = snapshot.get("sellwild_specifics") or {}
    vehicle = picks["category_id"] in VEHICLE_CATEGORIES

    def choice(path: str, label: str, value, options: list[dict], section: str, **extra) -> dict:
        return field(path, label, value, selection_only=True, options=[row["label"] for row in options],
                     option_values={row["label"]: str(row["id"]) for row in options}, options_source="marketplace",
                     section=section, **extra)

    # The category picker shows "Group | Category" once a category is picked.
    categories = [{"id": row["id"], "label": row["label"].replace(" > ", " | ", 1)} for row in catalog["categories"]]
    fields = [
        field("sellwild_specifics.title", "What are you selling?", _shared(snapshot, "sellwild", "title"), required=True,
              max_length=255, section="Add a Title"),
        field("price", "Price", snapshot.get("price"), required=True, input_type="number", min_value=5, max_value=MAX_PRICE,
              section="Name Your Price (USD)"),
        field("sellwild_specifics.description", "Describe your item (Optional)", _shared(snapshot, "sellwild", "description"),
              input_type="textarea", section="Add Details"),
        choice("sellwild_specifics.nativeCategoryId", "Category", picks["category_id"], categories, "Add Details", required=True),
        choice("sellwild_specifics.nativeConditionId", "Condition", picks["condition_id"], catalog["conditions"], "Add Details",
               required=True),
    ]
    if vehicle:
        # Vehicles add these two fields; their shipping section has only the ship-from address.
        fields.extend(field(f"sellwild_specifics.{key}", label, details.get(key), required=True, category_specific=True,
                            section="Vehicle Information", **extra)
                      for key, label, extra in (("vin", "Vehicle Identification Number", {}),
                                                ("mileage", "Mileage", {"input_type": "number", "min_value": 1})))
        return {"marketplace": "sellwild", "native": True, "known": True, "category_id": str(picks["category_id"] or ""),
                "fields": fields}
    # The package list: Custom Package Dimensions, the four flat-rate boxes, then "Add other packaging".
    template = str(details.get("parcelTemplate") or "")

    def package_label(row: dict) -> str:
        sides = [row[side] for side in ("length", "width", "height")]
        if row.get("featured") or not all(side > 0 for side in sides):
            return row["label"]
        return f"{row['label']} ({' × '.join(f'{side:g}' for side in sides)} in)"

    packages = [{"id": "", "label": "Custom Package Dimensions"},
                *({"id": row["id"], "label": package_label(row)} for row in catalog["parcel_templates"])]
    fields.append(choice("sellwild_specifics.parcelTemplate", "Package", template, packages, "Item Shipping",
                         category_specific=True))
    # Weight shows for every package that is not flat rate; the sides only for custom dimensions.
    # They start from the package in Item Details and are typed here, as on Sellwild's page.
    pounds = package_pounds(snapshot)
    sides = package_sides(snapshot) or []
    measured = (("weight", "Weight (lbs)", round(pounds, 2) if pounds else ""),
                *((key, f"{key.title()} (in)", sides[index] if sides else "")
                  for key, index in (("height", 2), ("width", 1), ("depth", 0))))
    fields.extend(field(f"sellwild_specifics.package.{key}", label, value, input_type="number", min_value=0,
                        category_specific=True, section="Item Shipping", width="half")
                  for key, label, value in measured)
    return {"marketplace": "sellwild", "native": True, "known": True, "category_id": str(picks["category_id"] or ""),
            "fields": fields}
