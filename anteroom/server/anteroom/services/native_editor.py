"""Editor definitions and explicit reloads from the catalogs used by direct List."""
from __future__ import annotations

import asyncio
import inspect
import logging
import re
from collections.abc import Awaitable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field as dc_field

import httpx
from sqlalchemy.orm import Session

from anteroom.repositories.queries import ConversationRepo, ListingRepo
from anteroom.services import direct_preflight
from anteroom.services.native_marketplaces import NativeMarketplaceError


_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FormSnapshot:
    """Plain data read in one short DB session before awaiting external marketplaces."""

    snapshot: dict
    listing_json: dict
    schemas: dict[tuple[str, str], list[dict] | None] = dc_field(default_factory=dict)
    choices: dict[str, dict | None] = dc_field(default_factory=dict)

    @contextmanager
    def cached_context(self) -> Iterator[None]:
        """Expose preloaded DB caches and one shared lazy eBay client for this form build."""
        from anteroom.services import marketplace_schemas, saved_choices

        with marketplace_schemas.schema_session(self.schemas), saved_choices.preload_entries(self.choices):
            yield


def _preload_form_caches(
    db: Session,
    snapshot: dict,
    marketplace: str,
) -> tuple[dict[tuple[str, str], list[dict] | None], dict[str, dict | None]]:
    from anteroom.services import category_fields, marketplace_schemas, saved_choices

    if marketplace == "ebay":
        from anteroom.services.ebay_listings import ebay_category_id
        from anteroom.services.listing_result import ListingProblem

        try:
            category = ebay_category_id(snapshot)
        except ListingProblem:
            category = ""
        schemas: dict[tuple[str, str], list[dict] | None] = {}
        if re.fullmatch(r"[1-9]\d{0,14}", category):
            schemas = category_fields.load_rows_many(
                (
                    (marketplace_schemas.EBAY_ASPECTS, category),
                    (marketplace_schemas.EBAY_CONDITIONS, category),
                    (marketplace_schemas.EBAY_LISTING_TYPES, category),
                    (marketplace_schemas.EBAY_LISTING_STRUCTURE, category),
                ),
                db=db,
            )
        return schemas, {"ebay-policies": saved_choices.read_entry("ebay-policies", db=db)}

    if marketplace == "etsy":
        from anteroom.services.direct_categories import direct_category_id

        category = direct_category_id(snapshot, "etsy")
        schemas = {}
        if re.fullmatch(r"[1-9]\d{0,14}", category):
            schemas = category_fields.load_rows_many(
                ((marketplace_schemas.ETSY_PROPERTIES, category),),
                db=db,
            )
        return schemas, {"etsy-profiles": saved_choices.read_entry("etsy-profiles", db=db)}

    return {}, {}


def _category_failure_types(exc: BaseException) -> str:
    """Fixed type vocabulary only: messages, custom type names and stacks can contain secrets."""
    from sqlalchemy.exc import SQLAlchemyError, TimeoutError as PoolTimeout
    from anteroom.services.ebay_client import EbayFailure
    from anteroom.services.marketplace_accounts import SignInRequired

    known = (EbayFailure, SignInRequired, PoolTimeout, SQLAlchemyError,
             httpx.ReadTimeout, httpx.ConnectTimeout, httpx.WriteTimeout, httpx.PoolTimeout,
             httpx.HTTPStatusError, httpx.TimeoutException, httpx.NetworkError, httpx.RequestError,
             TimeoutError, ValueError, KeyError, TypeError, RuntimeError, OSError, Exception)
    labels = []
    seen = set()
    for _ in range(4):
        if id(exc) in seen:
            break
        seen.add(id(exc))
        labels.append(next((kind.__name__ for kind in known if isinstance(exc, kind)), "BaseException"))
        cause = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
        if cause is None:
            break
        exc = cause
    return " > ".join(labels)


def form_snapshot(db: Session, conv_id: str, marketplace: str, revision_id: str | None) -> FormSnapshot:
    """The plain data a form is built from: the marketplace snapshot, saved listing, and cached schemas.

    Rolls back before returning, so no connection is held while eBay, Chrome
    or Mercari answer.
    """
    conv = ConversationRepo(db).get(conv_id)
    if not conv:
        raise NativeMarketplaceError(404, "Conversation not found")
    listing = ListingRepo(db).get_current(conv_id)
    revision = ListingRepo(db).get_revision(listing.current_revision_id) if listing else None
    if revision_id and (revision is None or revision.id != revision_id):
        raise NativeMarketplaceError(409, "The listing changed. Refresh its category choices.")
    # A draft nobody has generated yet still opens every form, empty, for the seller to fill in.
    listing_json = revision.listing_json if revision else {}
    snapshot = direct_preflight.prepare(db, conv, listing_json, marketplace)
    schemas, choices = _preload_form_caches(db, snapshot, marketplace)
    db.rollback()
    return FormSnapshot(snapshot=snapshot, listing_json=listing_json, schemas=schemas, choices=choices)


async def build_form(snapshot: dict, listing_json: dict, marketplace: str, *, refresh: bool = False) -> dict:
    """Never start a job or save a draft just to display the form."""
    if marketplace == "poshmark":
        from anteroom.services.poshmark_form import form

        return form(snapshot)
    if marketplace == "depop":
        from anteroom.services.depop_form import form

        return form(snapshot)
    if marketplace == "mercari":
        from anteroom.services.mercari_form import editor_form as mercari_form

        return await mercari_form(snapshot, refresh=refresh)
    if marketplace == "etsy":
        from anteroom.services.etsy_form import editor_form as etsy_form

        return await etsy_form(snapshot)
    if not refresh:
        from anteroom.services import native_contract

        saved_form = native_contract.editor_form(listing_json, marketplace, values=snapshot)
        if saved_form is not None:
            return saved_form
        # Stale saved choices must be rebuilt from the current catalog below.
        # Rejecting here leaves the editor empty with no way to reload them.
    if marketplace == "ebay":
        return await ebay_form(snapshot)
    raise NativeMarketplaceError(404, "This marketplace has no native editor form.")


async def editor_form(conv_id: str, marketplace: str, revision_id: str | None, *, refresh: bool = False) -> dict:
    """One marketplace's form, read in a short session of its own on a worker thread."""
    from anteroom.database import in_worker_session

    inputs = await in_worker_session(form_snapshot, conv_id, marketplace, revision_id)
    with inputs.cached_context():
        return await build_form(inputs.snapshot, inputs.listing_json, marketplace, refresh=refresh)


async def refresh_editor_form(conv_id: str, marketplace: str, revision_id: str) -> dict:
    """Rebuild one form off the DB session and fence its saved definition against concurrent edits."""
    from anteroom.database import in_worker_session

    form = await editor_form(conv_id, marketplace, revision_id, refresh=True)
    return await in_worker_session(save_refreshed_form, conv_id, marketplace, revision_id, form)


def read_form(db: Session, conv_id: str, marketplace: str, revision_id: str | None, *, refresh: bool = False) -> dict:
    """``editor_form`` for a plain caller with an existing session."""
    inputs = form_snapshot(db, conv_id, marketplace, revision_id)

    async def run() -> dict:
        with inputs.cached_context():
            return await build_form(inputs.snapshot, inputs.listing_json, marketplace, refresh=refresh)

    return asyncio.run(run())


def save_refreshed_form(db: Session, conv_id: str, marketplace: str, revision_id: str, form: dict) -> dict:
    from anteroom.repositories.queries import RevisionConflict
    from anteroom.services import listing_persistence, native_contract

    if form.get("known") is False or not form.get("fields"):
        raise NativeMarketplaceError(502, "The marketplace fields could not be loaded. Your saved listing is unchanged.")
    db.expire_all()
    repo = ListingRepo(db)
    current = repo.get_current(conv_id)
    if current is None or current.current_revision_id != revision_id:
        raise NativeMarketplaceError(409, "The listing changed while fields loaded. Reload its fields again.")
    revision = repo.get_revision(revision_id)
    if revision is None:
        raise NativeMarketplaceError(404, "Listing revision not found")
    listing = native_contract.copy_listing(revision.listing_json)
    existing_contracts = listing.get(native_contract.KEY) or {}
    captured = native_contract.capture(listing, {marketplace: form}, [marketplace])
    if captured.get(marketplace) != existing_contracts.get(marketplace):
        listing[native_contract.KEY] = {**existing_contracts, **captured}
        try:
            revision = listing_persistence.save_revision(
                db, conv_id, listing, "native_schema", parent_revision_id=revision_id, expected_revision_id=revision_id)
        except RevisionConflict as exc:
            db.rollback()
            raise NativeMarketplaceError(409, "The listing changed while fields loaded. Reload its fields again.") from exc
    return {"revision_id": revision.id}

def field(path: str, label: str, value, **extra) -> dict:
    return {"key": path, "path": path, "label": label, "value": value if value is not None else "",
            "required": False, "multi": False, "selection_only": False, "options": [], **extra}


# Each sell page's photo area (read 2026-10-28): its heading, empty where the page
# has none, and its hint. The photos, their order and the limit are the form's own
# pick in marketplace_photos (services/marketplace_media.py).
PAGE_PHOTOS = {
    "ebay": ("Photos & Video", "You can add up to 24 photos and a 1-minute video. Buyers want to see all details and angles."),
}


def photos_row(marketplace: str) -> dict:
    section, hint = PAGE_PHOTOS[marketplace]
    return field(f"marketplace_photos.{marketplace}", "", [], input_type="photos", help=hint, section=section)


def _shared(snapshot: dict, marketplace: str, name: str):
    """The form's own title, description, or brand, or General when this form has not set one."""
    from anteroom.services.seller_overrides import followed_entry

    entry = followed_entry(snapshot, marketplace, name)
    if entry is not None:
        return entry[1] if entry[1] is not None else ""
    return snapshot.get(name)


def package(snapshot: dict) -> list[dict]:
    """eBay's one shipping row: "Package weight" in lbs. and oz., then "Package dimensions", from Item Details."""
    return [field(key, label, snapshot.get(key), locked=True, help="Change the package in Item Details.",
                  width="quarter" if key.startswith("weight_") else "half")
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


async def _ebay_choices(snapshot: dict) -> list[dict]:
    """Payment, shipping and return policy and item location, as the page offers them for this listing.

    Each starts from the listing's own choice, then Settings, then the account's only one.
    """
    from anteroom.services import ebay_listings, saved_choices
    from anteroom.services.ebay_listings import LISTING_CHOICE_KEYS, own_listing_choices
    from anteroom.services.marketplace_accounts import account_id
    from anteroom.services.user_settings import get_ebay_listing_defaults

    try:
        # The saved copy opens the form at once; eBay is asked again behind it.
        profiles = await saved_choices.kept("ebay-policies", await account_id("ebay"), ebay_listings.listing_profiles)
        note = ""
    except Exception as exc:  # noqa: BLE001 - the rest of the form still loads; List checks these again
        profiles = {"fulfillment": [], "payment": [], "return": [], "locations": []}
        note = str(exc) if "access" in str(exc) else "eBay's policies could not be loaded. Reopen the listing to try again."
    own = own_listing_choices(snapshot)
    defaults = await asyncio.to_thread(get_ebay_listing_defaults)

    def row(key: str, kind: str, label: str, section: str) -> dict:
        options = profiles[kind]
        ids = [option["id"] for option in options]
        value = next((choice for choice in (own.get(key), defaults.get(LISTING_CHOICE_KEYS[key])) if choice in ids),
                     ids[0] if len(ids) == 1 else "")
        extra = {"help": note} if note else (
            {"help": "Provide additional information to have your listing show up in a buyer's local search."}
            if key == "merchantLocationKey" else {})
        return field(f"ebay_specifics.{key}", label, value, required=True, selection_only=True, section=section,
                     options=[option["title"] for option in options], options_source="marketplace",
                     option_values={option["title"]: option["id"] for option in options}, **extra)

    return [row("paymentPolicyId", "payment", "Payment policy", "Pricing"),
            row("fulfillmentPolicyId", "fulfillment", "Shipping policy", "Shipping"),
            row("merchantLocationKey", "locations", "Item location", "Preferences"),
            row("returnPolicyId", "return", "Return policy", "Preferences")]


async def ebay_form(snapshot: dict) -> dict:
    from anteroom.services import marketplace_schemas
    from anteroom.services.ebay_listings import (CHARITY_PERCENTS, CONDITION_IDS, best_offer_terms, condition_description,
                                                      ebay_aspects, ebay_category_id, ebay_condition, seller_sku)
    from anteroom.services.seller_overrides import price_for

    from anteroom.services.listing_result import ListingProblem

    try:
        category = ebay_category_id(snapshot)
    except ListingProblem:
        category = ""

    async def load_schema() -> list[dict]:
        try:
            return await marketplace_schemas.fields("ebay", category) if category else []
        except Exception as exc:  # noqa: BLE001 - never substitute a different form
            # Record before routes replace the cause with a safe HTTP response.
            # Never attach exc_info: even nested exception messages/URLs may carry credentials.
            # Suppress the raw chain too, in case another caller logs our safe wrapper.
            safe_category = category if re.fullmatch(r"[1-9][0-9]{0,14}", category) else "invalid"
            _log.error("eBay category schema load failed (category_id=%s; exception_types=%s)",
                       safe_category, _category_failure_types(exc))
            raise NativeMarketplaceError(502, "eBay’s category fields could not be loaded. Check its connection and refresh choices.") from None

    details = snapshot.get("ebay_specifics") or {}
    # All five independent eBay reads run concurrently and share one lazy client on cache misses.
    with marketplace_schemas.schema_session():
        schema_task = asyncio.create_task(load_schema())
        raw_schema, raw_variations, raw_selling, raw_choices = await asyncio.gather(
            schema_task,
            _ebay_variations_field(snapshot, category, schema_task),
            _ebay_format_fields(snapshot, category, details),
            _ebay_choices(snapshot),
            return_exceptions=True,
        )
    if isinstance(raw_schema, BaseException):
        raise raw_schema
    if isinstance(raw_variations, BaseException):
        raise raw_variations
    if isinstance(raw_selling, BaseException):
        raise raw_selling
    if isinstance(raw_choices, BaseException):
        raise raw_choices
    schema = raw_schema
    variations = raw_variations
    selling = raw_selling
    payment, shipping_policy, location, returns = raw_choices
    aspects = ebay_aspects(snapshot, snapshot.get("form") or {})

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
        extra = {}
        if name == "Personalization Instructions":
            extra = {"help": "Personalize is required before providing Personalization Instructions"}
        elif name == "Country of Origin":
            extra = {"help": "The country or territory where the finished item was manufactured, produced, or grown—not "
                             "where it ships from. This information may be needed for your listing to be visible to "
                             "international buyers."}
        elif condition:
            extra = {"help": "Disclose all flaws to prevent returns and earn better feedback."}
        return field(f"ebay_specifics.{name}", "Item condition" if condition else name, value, required=row["required"],
                     multi=row["multi"], section=section, **extra,
                     **({"category_specific": True} if not condition else {}),
                     selection_only=selection,
                     options=list(dict.fromkeys(v["label"] for v in values)),
                     option_values={v["label"]: str(v["id"]) for v in values}, options_source="marketplace",
                     max_values=row.get("max_values"), max_length=row.get("max_length"),
                     **({"value_constraints": constraints} if constraints else {}))

    terms = best_offer_terms(snapshot.get("form") or {}, str(price_for(snapshot, "ebay") or 0), snapshot)
    from anteroom.services import ebay_variations

    varied = ebay_variations.has_variations(snapshot)
    # Auction prices, offers and variations all travel with the category. The
    # editor shows the ones the current format uses, so a format change does
    # not load the form again.
    condition_row = next((row for row in schema if row.get("id") == "condition"), None)
    schedule = details.get("schedule")
    if not isinstance(schedule, dict):
        schedule = {}
    second_path = details.get("secondaryCategoryPath") or details.get("secondaryCategoryId")
    quantity = (field("quantity", "Quantity", ebay_variations.total_quantity(snapshot), input_type="number", locked=True,
                      help="The quantity is the total of the variations.") if varied else
                field("quantity", "Quantity", snapshot.get("quantity") or 1, input_type="number"))
    fields = [
        photos_row("ebay"),
        field("ebay_specifics.title", "Item title", _shared(snapshot, "ebay", "title"), required=True, max_length=80, section="Title",
              help="Provide a title for your item. Use words people would search for when looking for your item."),
        # eBay's "See title options" turns these on. Bold title is a $4 search upgrade the Inventory API does not take,
        # so it is not offered here: a switch that did not list would be a false control.
        field("ebay_specifics.subtitle", "Subtitle", details.get("subtitle"), max_length=55, section="Title",
              reveal="See title options",
              help="Subtitles appear in our search results in the list view, and can increase buyer interest by providing more "
                   "descriptive info. A listing upgrade fee of $2.00 applies whether or not your item sells."),
        field("sku", "Custom label (SKU)", seller_sku(snapshot), locked=True, section="Title", max_length=50,
              reveal="See title options",
              help="Create a custom label to enter information you want to track, such as your own SKU number. "
                   "It comes from Item Details."),
        field("direct_category_ids.ebay", "Category", (snapshot.get("direct_category_ids") or {}).get("ebay_path") or category,
              section="Item category", width="half", required=True),
        field("ebay_specifics.secondaryCategoryId", "Second category", second_path or "", section="Item category", width="half",
              help="Fees may apply whether or not your item sells."),
        field("ebay_specifics.primaryStoreCategory", "First category", details.get("primaryStoreCategory"), section="Store category",
              width="half", help="If you have an eBay Store, select the Store categories of the items you're listing."),
        field("ebay_specifics.secondaryStoreCategory", "Second category", details.get("secondaryStoreCategory"),
              section="Store category", width="half"),
        *[{**row, "section": "Variations"} for row in variations],
        *[{**aspect_field(row, EBAY_SPECIFIC_SECTIONS[tier]), **({"reveal": "Show more"} if tier == "optional" else {})}
          for row in ebay_specifics_order(schema)
          for tier in ["required" if row.get("required") else "recommended" if row.get("usage") == "RECOMMENDED" else "optional"]],
        field("ebay_specifics.upc", "UPC", details.get("upc"), max_length=20, section=EBAY_SPECIFIC_SECTIONS["optional"],
              reveal="Show more"),
        field("ebay_specifics.customAspectName", "Custom item specific", details.get("customAspectName") or "",
              section=EBAY_SPECIFIC_SECTIONS["optional"], width="half", reveal="Add custom item specific",
              help="Type in a unique detail that your buyers might look for."),
        field("ebay_specifics.customAspectValue", "Custom detail", details.get("customAspectValue") or "",
              section=EBAY_SPECIFIC_SECTIONS["optional"], width="half", reveal="Add custom item specific"),
        *([aspect_field(condition_row, "Condition")] if condition_row else []),
        field("ebay_specifics.conditionDescription", "Condition description", condition_description(snapshot, snapshot.get("form") or {}),
              input_type="textarea", max_length=1000, section="Condition",
              help="Let the buyer know what makes your item unique, including any interesting features or flaws."),
        field("ebay_specifics.description", "Item description", _shared(snapshot, "ebay", "description"), section="Description",
              help="Describe the item you're selling and provide complete and accurate details. Use a clear and concise "
                   "format to keep your description mobile-friendly."),
        *[{**row, "section": "Pricing"} for row in selling],
        field("price", "Item price", snapshot.get("price"), required=True, input_type="number", section="Pricing"),
        {**quantity, "section": "Pricing"},
        payment,
        field("ebay_specifics.bestOffer.enabled", "Allow offers (optional)", terms.get("bestOfferEnabled", True), input_type="boolean",
              section="Pricing", help="Interested buyers can send an offer for this item. You can accept, counter, or decline."),
        field("ebay_specifics.bestOffer.minimumOfferPrice", "Minimum offer", (terms.get("autoDeclinePrice") or {}).get("value", ""),
              input_type="number", section="Pricing", width="half"),
        field("ebay_specifics.bestOffer.autoAcceptPrice", "Auto accept", (terms.get("autoAcceptPrice") or {}).get("value", ""),
              input_type="number", section="Pricing", width="half"),
        field("ebay_specifics.schedule.enabled", "Schedule your listing", schedule.get("enabled") is True, input_type="boolean",
              section="Pricing", help="Your listing goes live immediately, unless you select a time and date you want it to start."),
        field("ebay_specifics.schedule.startAt", "Listing start time", schedule.get("startAt") or "", input_type="datetime-local",
              section="Pricing", width="half", help="Your local time."),
        field("ebay_specifics.salesTaxTable", "Sales tax",
              "Use the sales tax table to manage the tax rate for jurisdictions where you may have an obligation to collect sales tax from buyers.",
              locked=True, section="Pricing"),
        # The fields eBay's "See pricing options" panel turns on: Your cost, Sell as a lot, Private listing.
        field("cost", "Your cost", snapshot.get("cost"), input_type="number", section="Pricing", width="half",
              reveal="See pricing options",
              help="Enter your per-unit acquisition cost to see your earnings when the item sells. (Not visible to buyers.)"),
        field("ebay_specifics.lotSize", "Quantity in lot", details.get("lotSize") or "", input_type="number", min_value=2, step=1,
              section="Pricing", width="half", reveal="See pricing options",
              help="Group similar or identical items together to sell to a single buyer."),
        field("ebay_specifics.privateListing", "Private listing", details.get("privateListing") is True, input_type="boolean",
              section="Pricing", reveal="See pricing options",
              help="This allows buyers to remain anonymous when they bid on or buy an item."),
        shipping_policy,
        *[{**row, "section": "Shipping"} for row in package(snapshot)],
        field("ebay_specifics.irregularPackage", "Irregular package", details.get("irregularPackage") is True,
              input_type="boolean", section="Shipping",
              help="Carriers may charge extra for items not shipped in standard packages."),
        location,
        returns,
        field("ebay_specifics.charityId", "Charity", details.get("charityId") or "", section="Charity", width="half",
              help="Donate a portion to charity. You'll get a final value fee discount for sold items."),
        field("ebay_specifics.donationPercent", "Percentage", str(details.get("donationPercent") or ""),
              selection_only=True, section="Charity", width="half",
              options=[f"{percent}%" for percent in CHARITY_PERCENTS],
              option_values={f"{percent}%": percent for percent in CHARITY_PERCENTS},
              help="If your item sells, you will donate the percentage of your item's final sale price to the charity you chose."),
    ]
    return {"marketplace": "ebay", "native": True, "known": True, "category_id": category, "fields": fields}


async def _ebay_variations_field(
    snapshot: dict,
    category: str,
    schema: list[dict] | Awaitable[list[dict]],
) -> list[dict]:
    """Variations: what they differ by (aspects eBay enables for variations in this leaf) and one row each.

    Included whenever this category allows them. The editor hides the row during
    an auction that has no variation rows yet.
    """
    from anteroom.services import ebay_variations, marketplace_schemas

    varied = ebay_variations.has_variations(snapshot)
    value = ebay_variations.raw(snapshot) if varied else {"aspects": [], "rows": [], "photos": {}}
    if not category:
        return []
    supported: bool | None = None
    try:
        supported = await marketplace_schemas.ebay_variations_supported(None, category)
    except Exception:  # noqa: BLE001 - List checks it again before anything is written
        supported = None
    resolved_schema = await schema if inspect.isawaitable(schema) else schema
    aspects = [{"name": row["name"], "selection_only": row["mode"] == "selection",
                "options": list(dict.fromkeys(entry["label"] for entry in row.get("values") or []))}
               for row in resolved_schema if row.get("variations") and row.get("id") != "condition"]
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
    from anteroom.services import marketplace_schemas
    from anteroom.services.ebay_listings import (AUCTION, DEFAULT_AUCTION_DURATION, FIXED_PRICE, FORMAT_LABELS,
                                                      duration_label)

    kind = marketplace_schemas._ebay_listing_type(details.get("listingFormat")) or FIXED_PRICE
    allowed: dict[str, list[str]] = {}
    note = ""
    if category:
        try:
            allowed = await marketplace_schemas.ebay_listing_types(None, category)
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
