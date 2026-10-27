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
  const direct = asRecord(listing?.direct_category_ids);
  const mapped = asRecord(listing?.marketplace_category_ids);
  if (marketplace === "sellwild") return textOf(asRecord(listing?.sellwild_specifics).nativeCategoryId);
  if (marketplace === "ebay") {
    return textOf(direct.ebay) || textOf(mapped.ebay) || textOf(asRecord(listing?.ebay_specifics).categoryId);
  }
  if (marketplace === "depop") {
    return textOf(direct.depop) || textOf(asRecord(listing?.depop_specifics).categoryId) || textOf(mapped.depop);
  }
  return textOf(direct[marketplace]) || textOf(mapped[marketplace]);
}

/**
 * When this changes, the marketplace form has to be read again because the
 * category's rows are added or removed. A price, brand, format, or shipping
 * edit leaves it alone. The seller's category pick and an update that writes
 * a new category both change it, because both write the same category id.
 */
export function nativeFormStructureKey(listing: ListingData | undefined, marketplace: string): string {
  return nativeFormCategoryKey(listing, marketplace);
}

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
  // Mercari shows the label and free-shipping choice only with Prepaid label, and the minimum price only with Smart pricing on.
  if (context.tab === "mercari" && ["mercari_specifics.shippingClassId", "mercari_specifics.shippingPayerId"].includes(field.key)) {
    return shownChoice(context.fields, context.local, context.listing, "mercari_specifics.shippingMethod") !== "SOYO";
  }
  if (context.tab === "mercari" && field.key === "mercari_specifics.smartPricing.minimumPrice") {
    return shownChoice(context.fields, context.local, context.listing, "mercari_specifics.smartPricing.enabled") === "true";
  }
  if (context.tab === "ebay") {
    const auction = ebayFormat(context) === "AUCTION";
    const varied = ebayVaried(context.listing);
    if (EBAY_AUCTION_PRICE_KEYS.has(field.key)) return auction;
    if (EBAY_OFFER_KEYS.has(field.key)) return !auction && !varied;
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
  if (context.tab === "ebay" && field.key === "quantity") {
    if (ebayVaried(context.listing)) {
      return { ...field, locked: true, help: "The quantity is the total of the variations." };
    }
    if (ebayFormat(context) === "AUCTION") {
      return { ...field, locked: false, help: "An eBay auction sells one item." };
    }
    return { ...field, locked: false, help: undefined };
  }
  return field;
}

/** Sentences that follow the current format or shipping method, not the last form load. */
export function nativeFormNotes(
  tab: string,
  listing: ListingData | undefined,
  local: Record<string, string | string[] | undefined>,
  fields: readonly NativeFormRow[],
): string[] {
  const context: NativeFieldContext = { tab, listing, local, fields, categoryStale: false };
  if (tab === "ebay") {
    const notes: string[] = [];
    if (ebayFormat(context) === "AUCTION") notes.push("An auction uses Starting bid and Buy It Now, not Price.");
    if (ebayVaried(listing)) {
      notes.push("With variations, Price is the price of any variation left without its own. eBay takes no offers on a listing with variations.");
    }
    return notes;
  }
  return [];
}
