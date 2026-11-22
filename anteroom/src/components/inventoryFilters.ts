/** Inventory filters and sorts over Studio's own listing rows. */

import { soldAtMs } from "./sidebar/listingShelves";
import { hasLiveListing, inventoryStatus, liveMarketplaces } from "./sidebar/listingStatus";

export type ListingSortId =
  | "recent"
  | "created_desc"
  | "created_asc"
  | "modified_desc"
  | "modified_asc"
  | "title_asc"
  | "title_desc"
  | "price_desc"
  | "price_asc"
  | "sku_asc"
  | "sku_desc"
  | "stalest"
  | "freshest"
  | "sold_desc";

export const LISTING_SORTS: { id: ListingSortId; label: string }[] = [
  { id: "recent", label: "Recent activity" },
  { id: "created_desc", label: "Date Created (Newest)" },
  { id: "created_asc", label: "Date Created (Oldest)" },
  { id: "modified_desc", label: "Date Modified (Newest)" },
  { id: "modified_asc", label: "Date Modified (Oldest)" },
  { id: "title_asc", label: "Alphabetical (A-Z)" },
  { id: "title_desc", label: "Alphabetical (Z-A)" },
  { id: "price_desc", label: "Price (Highest)" },
  { id: "price_asc", label: "Price (Lowest)" },
  { id: "sku_asc", label: "SKU (A-Z)" },
  { id: "sku_desc", label: "SKU (Z-A)" },
  { id: "stalest", label: "Stalest (Listed longest ago)" },
  { id: "freshest", label: "Freshest (Listed most recently)" },
  { id: "sold_desc", label: "Date Sold (Newest)" },
];

/** The staleness cutoffs the filter offers, in days since the listing date. */
export const STALE_DAY_OPTIONS = [30, 60, 90] as const;

const DAY_MS = 86_400_000;

/**
 * The inventory's tabs, one per lifecycle state. Every listing sits under
 * exactly one of them: Active for what is live, Sold for what sold, and Draft
 * for everything not listed (a new draft, a send in progress or one that
 * failed, a delisted item, any status this build does not know).
 */
export const LISTING_STATUS_TABS = ["draft", "active", "sold"] as const;

export type ListingStatusFilter = (typeof LISTING_STATUS_TABS)[number];

export const LISTING_STATUS_LABELS: Record<ListingStatusFilter, string> = {
  draft: "Draft",
  active: "Active",
  sold: "Sold",
};

export type FilterableListing = {
  id?: string;
  settled_at?: string | null;
  title?: string | null;
  status?: string | null;
  sku?: string | null;
  price?: number | null;
  created_at?: string;
  updated_at?: string;
  vendoo_labels?: string[];
  vendoo_marketplaces?: string[];
  direct_marketplaces?: string[];
  sold_marketplaces?: string[];
  removed_marketplaces?: string[];
  sold_at?: string | null;
  /** When the longest-standing live listing went up. A relist starts its marketplace over. */
  oldest_listed_at?: string | null;
  unsent_edits?: boolean;
  /** Sale details the sales records still lack, such as fees or the sale date. */
  missing_sale_data?: string[];
  /** Marketplaces where the listing is still up but not for sale. */
  inactive_marketplaces?: string[];
  /** Marketplace prices that differ from the seller's pricing rules. */
  price_drift?: { marketplace: string; expected?: number; price?: number; source?: string }[];
};

export interface ListingFilters {
  status: ListingStatusFilter;
  marketplaces: string[];
  /** Labels a listing must wear one of (✓). */
  labels: string[];
  /** Labels a listing must not wear (✗), alongside the included ones. */
  excludeLabels: string[];
  /**
   * Turns the marketplace picks around: keep the listings missing from one of
   * them, or, with none picked, the listings no marketplace carries.
   */
  notListed: boolean;
  /** Keep only listings edited here since they were last sent to a marketplace. */
  unsent: boolean;
  /** Keep only listings whose sale is missing details, such as fees or the sale date. */
  missingSale: boolean;
  /** Keep only listings still up somewhere but not for sale there. */
  inactive: boolean;
  /** Keep only listings whose marketplace prices drifted from the pricing rules. */
  priceDrift: boolean;
  /** Keep only listings without a SKU. */
  missingSku: boolean;
  /** Keep only listings that went live at least this many days ago; 0 is off. */
  staleDays: number;
  sort: ListingSortId;
}

export const DEFAULT_LISTING_FILTERS: ListingFilters = {
  status: "active",
  marketplaces: [],
  labels: [],
  excludeLabels: [],
  notListed: false,
  unsent: false,
  missingSale: false,
  inactive: false,
  priceDrift: false,
  missingSku: false,
  staleDays: 0,
  sort: "recent",
};

/** What an empty tab says when nothing is searched or filtered: "No draft listings". */
export function emptyTabLabel(tab: ListingStatusFilter): string {
  return `No ${LISTING_STATUS_LABELS[tab].toLowerCase()} listings`;
}

/** The tab a listing belongs to. Anything not live or sold files under Draft. */
export function listingStatusTab(status?: string | null): ListingStatusFilter {
  const value = String(status || "draft");
  return value === "active" || value === "sold" ? value : "draft";
}

/**
 * The tab a listing belongs to, taking live marketplaces into account: a live
 * item stays under Active while a background job ("listing") runs on it,
 * keeping its Listing label until the job finishes.
 */
export function listingTab(listing: {
  status?: string | null;
  vendoo_marketplaces?: string[];
  direct_marketplaces?: string[];
  removed_marketplaces?: string[];
  sold_marketplaces?: string[];
}): ListingStatusFilter {
  const status = inventoryStatus(listing);
  if (status === "active" || status === "sold") return status;
  if (status === "listing" && hasLiveListing(listing)) return "active";
  return "draft";
}

/**
 * Whether a listing shows under a status tab. The tabs are lifecycle states and
 * nothing else: a live listing with unsent edits or an active send still
 * belongs to Active, where its tag and label explain what is happening. A
 * failed send keeps its Failed label on its row under Draft.
 */
export function inStatusTab(listing: FilterableListing, tab: ListingStatusFilter): boolean {
  return listingTab(listing) === tab;
}

function lower(value: string | null | undefined): string {
  return String(value || "").toLowerCase();
}

/**
 * The marketplaces an item is on for the filter: live there, or sold there.
 * A sold listing is not live, but the Sold tab is still searched by where it
 * sold, and a sold item has nothing left to list there.
 */
function listedOn(listing: FilterableListing): string[] {
  return [...new Set([...liveMarketplaces(listing), ...(listing.sold_marketplaces || [])])];
}

/** Search covers title, SKU and the labels a listing wears. */
export function matchesSearch(listing: FilterableListing, needle: string): boolean {
  if (!needle) return true;
  return (
    lower(listing.title || "Untitled").includes(needle)
    || lower(listing.sku).includes(needle)
    || (listing.vendoo_labels || []).some((label) => lower(label).includes(needle))
  );
}

/**
 * When the longest-standing live listing went up. An item is only as fresh as
 * its stalest live marketplace; the server works it out from imported dates
 * and Anteroom's own listings, so a relist counts.
 */
export function oldestLiveListedMs(listing: FilterableListing): number {
  return timeMs(listing.oldest_listed_at);
}

/** Days since the oldest live listing went up, or null for one that never did. */
export function listedDaysAgo(listing: FilterableListing, now: number = Date.now()): number | null {
  const listed = oldestLiveListedMs(listing);
  return listed ? Math.floor((now - listed) / DAY_MS) : null;
}

/**
 * A stale listing is one still sitting unsold a long time after it went live.
 * A sold item is a finished story rather than a stale one, and an item with no
 * listing date has never gone live, so neither can be stale.
 */
export function isStale(listing: FilterableListing, days: number, now: number = Date.now()): boolean {
  if (days <= 0) return true;
  if (String(listing.status || "draft") === "sold") return false;
  const age = listedDaysAgo(listing, now);
  return age !== null && age >= days;
}

export function matchesFilters(
  listing: FilterableListing,
  filters: ListingFilters,
  now: number = Date.now(),
): boolean {
  if (!inStatusTab(listing, filters.status)) return false;
  const listed = listedOn(listing);
  if (filters.notListed) {
    // Vendoo's "View Not Listed" asks for the items no marketplace carries.
    // Picking marketplaces narrows the question to those: what still has to go
    // up on any one of them.
    if (!filters.marketplaces.length) {
      if (listed.length) return false;
    } else if (filters.marketplaces.every((id) => listed.includes(id))) return false;
  } else if (filters.marketplaces.length && !filters.marketplaces.some((id) => listed.includes(id))) return false;
  if (filters.labels.length || filters.excludeLabels?.length) {
    const own = new Set((listing.vendoo_labels || []).map(lower));
    if (filters.labels.length && !filters.labels.some((label) => own.has(lower(label)))) return false;
    // Include and exclude together: "Summer" but not "Consigned".
    if ((filters.excludeLabels || []).some((label) => own.has(lower(label)))) return false;
  }
  if (filters.unsent && !listing.unsent_edits) return false;
  if (filters.missingSale && !listing.missing_sale_data?.length) return false;
  if (filters.inactive && !listing.inactive_marketplaces?.length) return false;
  if (filters.priceDrift && !listing.price_drift?.length) return false;
  if (filters.missingSku && !missingSku(listing)) return false;
  if (!isStale(listing, filters.staleDays, now)) return false;
  return true;
}

export function filterListings<T extends FilterableListing>(
  listings: T[],
  needle: string,
  filters: ListingFilters,
  now: number = Date.now(),
): T[] {
  return listings.filter(
    (listing) => matchesSearch(listing, needle) && matchesFilters(listing, filters, now),
  );
}

function timeMs(value?: string | null): number {
  if (!value) return 0;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
}

function byText(left: string | null | undefined, right: string | null | undefined, direction: 1 | -1): number {
  const a = String(left || "");
  const b = String(right || "");
  // A listing without a SKU sorts last in either direction, the way Vendoo's
  // own SKU sorts leave unsku'd items at the end.
  if (!a !== !b) return a ? -1 : 1;
  return a.localeCompare(b, undefined, { sensitivity: "base", numeric: true }) * direction;
}

function byNumber(left: number | null | undefined, right: number | null | undefined, direction: 1 | -1): number {
  const a = typeof left === "number" ? left : null;
  const b = typeof right === "number" ? right : null;
  // A listing without a price sorts last in either direction, as an unpriced
  // draft is never what a price sort is looking for.
  if (a === null || b === null) return a === b ? 0 : a === null ? 1 : -1;
  return (a - b) * direction;
}

/** Order for every sort but `recent`, which keeps each shelf's own anchor. */
export function compareListings(sort: ListingSortId): ((left: FilterableListing, right: FilterableListing) => number) | null {
  switch (sort) {
    case "created_desc":
      return (l, r) => timeMs(r.created_at) - timeMs(l.created_at);
    case "created_asc":
      return (l, r) => timeMs(l.created_at) - timeMs(r.created_at);
    case "modified_desc":
      return (l, r) => timeMs(r.updated_at) - timeMs(l.updated_at);
    case "modified_asc":
      return (l, r) => timeMs(l.updated_at) - timeMs(r.updated_at);
    case "title_asc":
      return (l, r) => byText(l.title || "Untitled", r.title || "Untitled", 1);
    case "title_desc":
      return (l, r) => byText(l.title || "Untitled", r.title || "Untitled", -1);
    case "price_desc":
      return (l, r) => byNumber(l.price, r.price, -1);
    case "price_asc":
      return (l, r) => byNumber(l.price, r.price, 1);
    case "sku_asc":
      return (l, r) => byText(l.sku, r.sku, 1);
    case "sku_desc":
      return (l, r) => byText(l.sku, r.sku, -1);
    // A listing that never went live has no staleness to compare, so it sorts
    // last either way rather than pretending to be the oldest.
    case "stalest":
      return (l, r) => byNumber(oldestLiveListedMs(l) || null, oldestLiveListedMs(r) || null, 1);
    case "freshest":
      return (l, r) => byNumber(oldestLiveListedMs(l) || null, oldestLiveListedMs(r) || null, -1);
    case "sold_desc":
      return (l, r) => byNumber(soldAtMs(l) || null, soldAtMs(r) || null, -1);
    default:
      return null;
  }
}

/**
 * The status tab a sort belongs on. Only sold listings have a sale date, so
 * sorting by it opens the Sold tab; every other sort keeps the current tab.
 */
export function statusForSort(sort: ListingSortId, status: ListingStatusFilter): ListingStatusFilter {
  return sort === "sold_desc" ? "sold" : status;
}

export function sortListings<T extends FilterableListing>(listings: T[], sort: ListingSortId): T[] {
  const compare = compareListings(sort);
  return compare ? [...listings].sort(compare) : listings;
}

/**
 * Finished listings (sold ones, and duplicates merged into another item) stay
 * out of the working list, but remain findable in search and Sold.
 */
export function visibleInInventory(
  listing: FilterableListing,
  tab: string,
  searching = false,
  selectedId: string | null = null,
): boolean {
  return !listing.settled_at || searching || tab === "sold" || Boolean(selectedId && listing.id === selectedId);
}

export function statusCounts(
  listings: FilterableListing[],
  searching = false,
  selectedId: string | null = null,
  retainedTab?: ListingStatusFilter,
): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const listing of listings) {
    const status = retainedTab && listing.id === selectedId ? retainedTab : listingTab(listing);
    if (!visibleInInventory(listing, status, searching, selectedId)) continue;
    counts[status] = (counts[status] || 0) + 1;
  }
  return counts;
}

/** Every label in the inventory, deduped case-insensitively. */
export function labelOptions(listings: FilterableListing[]): string[] {
  const seen = new Map<string, string>();
  for (const listing of listings) {
    for (const label of listing.vendoo_labels || []) {
      const key = lower(label);
      if (key && !seen.has(key)) seen.set(key, label);
    }
  }
  return [...seen.values()].sort((left, right) => left.localeCompare(right, undefined, { sensitivity: "base" }));
}

/**
 * Marketplace rows: the ones Settings shows, kept in that order, plus any other
 * the inventory uses. A marketplace nothing sits on yet is only worth a row when
 * the question is what is missing from it, which is what `includeUnused` asks.
 */
export function marketplaceOptions(
  listings: FilterableListing[],
  preferred: string[],
  includeUnused = false,
): string[] {
  const used = new Set<string>();
  for (const listing of listings) {
    for (const id of listedOn(listing)) used.add(id);
  }
  const ordered = includeUnused ? preferred : preferred.filter((id) => used.has(id));
  const extra = [...used].filter((id) => !preferred.includes(id)).sort();
  return [...ordered, ...extra];
}

export function activeFilterCount(filters: ListingFilters): number {
  // The selected status tab already shows itself, so the Filters badge counts
  // only the menu filters underneath it.
  return (
    // "Not listed on eBay" is one narrowing, not two.
    Math.max(filters.marketplaces.length, filters.notListed ? 1 : 0) +
    filters.labels.length +
    (filters.excludeLabels?.length || 0) +
    (filters.unsent ? 1 : 0) +
    (filters.missingSale ? 1 : 0) +
    (filters.inactive ? 1 : 0) +
    (filters.priceDrift ? 1 : 0) +
    (filters.missingSku ? 1 : 0) +
    (filters.staleDays ? 1 : 0)
  );
}

/**
 * A count short enough to sit in a status tab. Three tabs and their counts share
 * a sidebar that narrows to 200px, where "1121" is what pushes the labels into
 * each other.
 */
export function compactCount(value: number): string {
  if (!Number.isFinite(value) || value < 1000) return String(Math.max(0, Math.trunc(value || 0)));
  const thousands = value / 1000;
  if (thousands >= 100) return `${Math.round(thousands)}k`;
  return `${thousands.toFixed(1).replace(/\.0$/, "")}k`;
}

export function toggleValue(values: string[], value: string): string[] {
  return values.includes(value) ? values.filter((entry) => entry !== value) : [...values, value];
}

/** How many of these listings each marketplace carries, for the filter rows. */
export function marketplaceCounts(listings: FilterableListing[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const listing of listings) {
    for (const id of new Set(listedOn(listing))) counts[id] = (counts[id] || 0) + 1;
  }
  return counts;
}

/** How many of these listings each of the marketplaces is missing from. */
export function notListedOnCounts(listings: FilterableListing[], marketplaces: string[]): Record<string, number> {
  const carried = marketplaceCounts(listings);
  return Object.fromEntries(marketplaces.map((id) => [id, listings.length - (carried[id] || 0)]));
}

/** The same for labels, keyed the way `labelOptions` names them. */
export function labelCounts(listings: FilterableListing[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const listing of listings) {
    for (const label of new Set((listing.vendoo_labels || []).map(lower))) {
      counts[label] = (counts[label] || 0) + 1;
    }
  }
  return counts;
}

/** How many listings each staleness cutoff would keep, including the seller's own stale threshold. */
export function staleCounts(
  listings: FilterableListing[],
  now: number = Date.now(),
  thresholdDays?: number,
): Record<number, number> {
  const counts: Record<number, number> = {};
  const options = [...new Set([...STALE_DAY_OPTIONS, ...(thresholdDays ? [thresholdDays] : [])])].sort((a, b) => a - b);
  for (const days of options) {
    counts[days] = listings.filter((listing) => isStale(listing, days, now)).length;
  }
  return counts;
}

/** Listings edited since they were last sent to a marketplace. */
export function unsentCount(listings: FilterableListing[]): number {
  return listings.filter((listing) => Boolean(listing.unsent_edits)).length;
}

/** Listings whose sale is missing details. */
export function missingSaleCount(listings: FilterableListing[]): number {
  return listings.filter((listing) => Boolean(listing.missing_sale_data?.length)).length;
}

/** Listings no marketplace carries. */
export function notListedCount(listings: FilterableListing[]): number {
  return listings.filter((listing) => listedOn(listing).length === 0).length;
}

/** A listing with no SKU to find it by on the shelf. */
export function missingSku(listing: FilterableListing): boolean {
  return !String(listing.sku || "").trim();
}

export type LabelFilterState = "include" | "exclude" | "none";

/** Whether a label is included (✓), excluded (✗) or neither. */
export function labelFilterState(filters: ListingFilters, label: string): LabelFilterState {
  const key = label.toLowerCase();
  if (filters.labels.some((value) => value.toLowerCase() === key)) return "include";
  if (filters.excludeLabels?.some((value) => value.toLowerCase() === key)) return "exclude";
  return "none";
}

/** One click moves a label from off, to included, to excluded, and back off. */
export function cycleLabelFilter(filters: ListingFilters, label: string): ListingFilters {
  const state = labelFilterState(filters, label);
  const key = label.toLowerCase();
  const labels = filters.labels.filter((value) => value.toLowerCase() !== key);
  const excludeLabels = (filters.excludeLabels || []).filter((value) => value.toLowerCase() !== key);
  if (state === "none") return { ...filters, labels: [...labels, label], excludeLabels };
  if (state === "include") return { ...filters, labels, excludeLabels: [...excludeLabels, label] };
  return { ...filters, labels, excludeLabels };
}

/** Listings still up somewhere but not for sale there. */
export function inactiveCount(listings: FilterableListing[]): number {
  return listings.filter((listing) => Boolean(listing.inactive_marketplaces?.length)).length;
}

/** Listings whose marketplace prices drifted from the pricing rules. */
export function priceDriftCount(listings: FilterableListing[]): number {
  return listings.filter((listing) => Boolean(listing.price_drift?.length)).length;
}

/** Listings without a SKU. */
export function missingSkuCount(listings: FilterableListing[]): number {
  return listings.filter(missingSku).length;
}
