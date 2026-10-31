import type { ListingData } from "../api/types";
import { editorValuePresent, formFieldText, formFieldValues, getListingEditorValue } from "../listingPaths";

/** The parts of a native field this module needs. The editor's rows are a superset. */
export interface NativeFormRow {
  key: string;
  locked?: boolean;
  selectionOnly?: boolean;
  multi?: boolean;
  options?: string[];
  optionValues?: Record<string, string>;
  defaultValue?: unknown;
  help?: string;
  /** True when this row exists only for the selected category. */
  categorySpecific?: boolean;
  /** Discount id → dollars the seller pays. Your Earnings subtracts the selected one. */
  optionAmounts?: Record<string, number>;
}

/** Poshmark's seller fee, read 2026-10-27: $2.95 under $15, 20% from $15, then the seller's shipping discount, floored at $0. */
export function poshmarkEarnings(price: unknown, discount = 0): string {
  const amount = Number(price);
  if (!Number.isFinite(amount) || amount <= 0) return "";
  const cents = Math.round(amount * 100);
  const fee = amount < 15 ? 295 : Math.round(amount * 20);
  const off = Math.round(Number(discount) * 100) || 0;
  return `$${(Math.max(0, cents - fee - off) / 100).toFixed(2)}`;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function textOf(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return "";
}

/**
 * The category id that decides which category-specific rows this marketplace shows.
 * Price, brand, condition and the other saved values are not part of it.
 */
export function nativeFormCategoryKey(listing: ListingData | undefined, marketplace: string): string {
  if (marketplace === "sellwild") return textOf(asRecord(listing?.sellwild_specifics).nativeCategoryId);
  // The tab's own category only. Vendoo's category ids belong to Send to Vendoo.
  return textOf(asRecord(listing?.direct_category_ids)[marketplace]);
}

/**
 * When this changes, the marketplace form has to be read again because the
 * category's rows are added or removed. A price, brand, format, or shipping
 * edit leaves it alone. The seller's category pick and an update that writes
 * a new category both change it, because both write the same category id.
 */
export function nativeFormStructureKey(listing: ListingData | undefined, marketplace: string): string {
  const category = nativeFormCategoryKey(listing, marketplace);
  if (marketplace !== "mercari") return category;
  // Mercari's labels depend on the package: its picker asks for them again when the weight or size changes.
  const specifics = asRecord(listing?.mercari_specifics);
  return [category, textOf(specifics.shippingMethod), JSON.stringify(specifics.package ?? null),
    textOf(listing?.weight_lb), textOf(listing?.weight_oz), textOf(listing?.package_dimensions_in)].join("|");
}

const MERCARI_LABEL_KEYS = new Set(["mercari_specifics.shippingClassId", "mercari_specifics.shippingPayerId"]);
const MERCARI_SIDE_KEYS = new Set(["mercari_specifics.package.length", "mercari_specifics.package.width", "mercari_specifics.package.height"]);

/** The previous category's rows are still on screen while the new category's form loads. */
export function categorySpecificFieldsStale(
  loadedCategoryId: string | undefined,
  categoryKey: string,
  placeholder: boolean,
): boolean {
  if (!placeholder) return false;
  return String(loadedCategoryId || "") !== categoryKey;
}

const CATEGORY_MARKETS = ["ebay", "poshmark", "mercari", "depop", "etsy", "vinted", "grailed", "facebook", "sellwild"];

/** Every saved category id. A price or brand edit does not change it. */
export function listingCategorySignature(listing: ListingData | undefined): string {
  const direct = asRecord(listing?.direct_category_ids);
  const mapped = asRecord(listing?.marketplace_category_ids);
  const paths = asRecord(listing?.marketplace_categories);
  return CATEGORY_MARKETS.map((market) => [
    textOf(direct[market]),
    textOf(mapped[market]),
    textOf(paths[market]),
    market === "depop" ? textOf(asRecord(listing?.depop_specifics).categoryId) : "",
    market === "ebay" ? textOf(asRecord(listing?.ebay_specifics).categoryId) : "",
    market === "sellwild" ? textOf(asRecord(listing?.sellwild_specifics).nativeCategoryId) : "",
  ].join(":")).join("\n");
}

function choiceMatches(field: NativeFormRow, value: unknown): boolean {
  const values = field.multi ? formFieldValues(value, field.options) : [formFieldText(value)].filter(Boolean);
  if (!values.length) return false;
  const ids = new Set(Object.values(field.optionValues || {}));
  return values.every((item) => field.options?.includes(item) || ids.has(item));
}

/**
 * What a control shows. A saved listing value wins, so editing it does not wait
 * on another form load. A locked row keeps the form's computed value. A
 * selection whose saved text is not one of the choices keeps the form's
 * resolved id until the seller picks a choice.
 */
export function resolvedEditorValue(field: NativeFormRow, listing: ListingData | undefined): unknown {
  // eBay's category control shows the breadcrumb. The id lives beside it.
  if (field.key === "direct_category_ids.ebay") {
    const path = getListingEditorValue(listing, "direct_category_ids.ebay_path");
    if (editorValuePresent(path)) return path;
    if (field.defaultValue !== undefined) return field.defaultValue;
  }
  const listingValue = getListingEditorValue(listing, field.key);
  if (field.locked && field.defaultValue !== undefined) return field.defaultValue;
  if (listingValue !== undefined && !editorValuePresent(listingValue)) return listingValue;
  if (editorValuePresent(listingValue) && (!field.selectionOnly || choiceMatches(field, listingValue))) return listingValue;
  if (field.defaultValue !== undefined) return field.defaultValue;
  return listingValue;
}

function shownChoice(
  fields: readonly NativeFormRow[],
  local: Record<string, string | string[] | undefined>,
  listing: ListingData | undefined,
  key: string,
): string {
  const field = fields.find((row) => row.key === key);
  const typed = local[key];
  const shown = typed != null
    ? formFieldText(typed)
    : formFieldText(field ? resolvedEditorValue(field, listing) : getListingEditorValue(listing, key));
  return field?.optionValues?.[shown] ?? shown;
}

/** Sellwild's packages that are not flat rate, so its page still asks for a weight. */
const SELLWILD_WEIGHED_PACKAGES = new Set(["USPS_SoftPack", "USPS_IrregularParcel"]);

const EBAY_AUCTION_PRICE_KEYS = new Set([
  "ebay_specifics.auctionStartPrice",
  "ebay_specifics.auctionReservePrice",
  "ebay_specifics.auctionBuyItNow",
]);
const EBAY_OFFER_KEYS = new Set([
  "ebay_specifics.bestOffer.enabled",
  "ebay_specifics.bestOffer.autoAcceptPrice",
  "ebay_specifics.bestOffer.minimumOfferPrice",
]);
const GTC = "GTC";
const GTC_LABEL = "Good 'Til Cancelled";

export interface NativeFieldContext {
  tab: string;
  listing: ListingData | undefined;
  local: Record<string, string | string[] | undefined>;
  fields: readonly NativeFormRow[];
  categoryStale: boolean;
}

function ebayFormat(context: NativeFieldContext): string {
  return shownChoice(context.fields, context.local, context.listing, "ebay_specifics.listingFormat") === "AUCTION"
    ? "AUCTION"
    : "FIXED_PRICE";
}

function ebayVaried(listing: ListingData | undefined): boolean {
  const rows = asRecord(asRecord(listing?.ebay_specifics).variations).rows;
  return Array.isArray(rows) && rows.length > 0;
}

/** Rows to paint. Category attributes wait for the category that owns them. Toggles reveal their own row without a reload. */
export function nativeFieldVisible(field: NativeFormRow, context: NativeFieldContext): boolean {
  if (context.categoryStale && field.categorySpecific) return false;
  if (field.key === "depop_specifics.worldwideShipping.price") {
    return shownChoice(context.fields, context.local, context.listing, "depop_specifics.worldwideShipping.enabled") === "true";
  }
  if (field.key.startsWith("sellwild_specifics.package.")) {
    // Sellwild's page: Custom Package Dimensions asks for weight and all three sides,
    // a package that is not flat rate asks for weight only, a flat-rate one for neither.
    const template = shownChoice(context.fields, context.local, context.listing, "sellwild_specifics.parcelTemplate");
    if (!template) return true;
    return field.key === "sellwild_specifics.package.weight" && SELLWILD_WEIGHED_PACKAGES.has(template);
  }
  // Mercari shows the label, its package questions and the free-shipping choice only with Prepaid label.
  // It asks for the sides, or lets you skip them, only once the item does not fit in a shoebox,
  // and the minimum price only with Smart pricing on.
  if (context.tab === "mercari" && (MERCARI_LABEL_KEYS.has(field.key) || field.key.startsWith("mercari_specifics.package."))) {
    if (shownChoice(context.fields, context.local, context.listing, "mercari_specifics.shippingMethod") === "SOYO") return false;
    const notShoebox = shownChoice(context.fields, context.local, context.listing, "mercari_specifics.package.fitsShoebox") === "no";
    if (field.key === "mercari_specifics.package.skipDimensions") return notShoebox;
    if (MERCARI_SIDE_KEYS.has(field.key)) {
      return notShoebox && shownChoice(context.fields, context.local, context.listing, "mercari_specifics.package.skipDimensions") !== "true";
    }
    return true;
  }
  // Poshmark asks for the lowest price only once Smart Sell is on, and for a drop time only for Drops.
  if (context.tab === "poshmark" && field.key === "poshmark_specifics.smartSell.minimumPrice") {
    return shownChoice(context.fields, context.local, context.listing, "poshmark_specifics.smartSell.enabled") === "true";
  }
  if (context.tab === "poshmark" && field.key === "poshmark_specifics.makeAvailableAt") {
    return shownChoice(context.fields, context.local, context.listing, "poshmark_specifics.availability") === "coming_soon";
  }
  if (context.tab === "mercari" && field.key === "mercari_specifics.smartPricing.minimumPrice") {
    return shownChoice(context.fields, context.local, context.listing, "mercari_specifics.smartPricing.enabled") === "true";
  }
  if (context.tab === "ebay" && field.key === "ebay_specifics.schedule.startAt") {
    return shownChoice(context.fields, context.local, context.listing, "ebay_specifics.schedule.enabled") === "true";
  }
  if (context.tab === "ebay") {
    const auction = ebayFormat(context) === "AUCTION";
    const varied = ebayVaried(context.listing);
    // eBay's page: an auction has a duration and bids instead of an item price; Buy It Now runs until cancelled.
    if (EBAY_AUCTION_PRICE_KEYS.has(field.key) || field.key === "ebay_specifics.listingDuration") return auction;
    if (field.key === "ebay_specifics.price") return !auction;
    if (EBAY_OFFER_KEYS.has(field.key)) {
      if (auction || varied) return false;
      // Minimum offer and Auto accept show only while Allow offers is on.
      if (field.key === "ebay_specifics.bestOffer.enabled") return true;
      return shownChoice(context.fields, context.local, context.listing, "ebay_specifics.bestOffer.enabled") === "true";
    }
    if (field.key === "ebay_specifics.variations") return !(auction && !varied);
  }
  return true;
}

function presentDuration<T extends NativeFormRow>(field: T, auction: boolean): T {
  const optionValues = field.optionValues || {};
  const options = field.options || [];
  if (!auction) {
    return {
      ...field,
      locked: true,
      selectionOnly: true,
      options: [GTC_LABEL],
      optionValues: { [GTC_LABEL]: GTC },
      defaultValue: GTC,
      help: "eBay fixed-price listings run Good 'Til Cancelled.",
    };
  }
  const auctionOptions = options.filter((label) => optionValues[label] && optionValues[label] !== GTC);
  if (!auctionOptions.length) return { ...field, locked: false };
  const auctionValues = Object.fromEntries(auctionOptions.map((label) => [label, optionValues[label]]));
  const current = formFieldText(field.defaultValue);
  const currentId = optionValues[current] ?? current;
  const fallback = auctionValues["7 days"] || auctionValues[auctionOptions[0]];
  return {
    ...field,
    locked: false,
    selectionOnly: true,
    options: auctionOptions,
    optionValues: auctionValues,
    defaultValue: Object.values(auctionValues).includes(currentId) ? currentId : fallback,
    help: undefined,
  };
}

function presentFormat<T extends NativeFormRow>(field: T, varied: boolean, format: string): T {
  if (!varied || format === "AUCTION") return field;
  const optionValues = field.optionValues || {};
  const options = (field.options || []).filter((label) => optionValues[label] !== "AUCTION");
  if (!options.length || options.length === (field.options || []).length) return field;
  return {
    ...field,
    options,
    optionValues: Object.fromEntries(options.map((label) => [label, optionValues[label]])),
    help: "A listing with variations is fixed price. Remove the variations to sell at auction.",
  };
}

/**
 * The category payload includes every row that category can show. Format,
 * variations, and shipping method decide which of those rows are locked or
 * which choices they offer, without asking for the form again.
 */
export function presentNativeField<T extends NativeFormRow>(field: T, context: NativeFieldContext): T {
  if (context.tab === "ebay" && field.key === "ebay_specifics.listingDuration") {
    return presentDuration(field, ebayFormat(context) === "AUCTION");
  }
  if (context.tab === "ebay" && field.key === "ebay_specifics.listingFormat") {
    return presentFormat(field, ebayVaried(context.listing), ebayFormat(context));
  }
  if (context.tab === "poshmark" && field.key === "poshmark_specifics.earnings") {
    const priceField = context.fields.find((row) => row.key === "poshmark_specifics.poshmarkPrice" || row.key === "price");
    const typed = priceField && context.local[priceField.key] != null ? context.local[priceField.key] : context.local.price;
    const price = typed != null ? formFieldText(typed) : formFieldText(getListingEditorValue(context.listing, priceField?.key || "price") ?? getListingEditorValue(context.listing, "price"));
    const discountId = shownChoice(context.fields, context.local, context.listing, "poshmark_specifics.shippingDiscountId");
    const shipping = context.fields.find((row) => row.key === "poshmark_specifics.shippingDiscountId");
    const discount = Number(shipping?.optionAmounts?.[discountId] || 0);
    return { ...field, locked: true, defaultValue: poshmarkEarnings(price, discount) };
  }
  if (context.tab === "ebay" && field.key === "quantity") {
    if (ebayVaried(context.listing)) {
      return { ...field, locked: true, help: "The quantity is the total of the variations." };
    }
    return { ...field, locked: false, help: undefined };
  }
  return field;
}

/** Sentences that follow the current variations, not the last form load. */
export function nativeFormNotes(tab: string, listing: ListingData | undefined): string[] {
  if (tab === "ebay" && ebayVaried(listing)) {
    return ["With variations, Price is the price of any variation left without its own. eBay takes no offers on a listing with variations."];
  }
  return [];
}
