/** What the inventory table shows for one listing on one marketplace. */
import type { DirectRecord } from "../api/types";
import {
  DEFAULT_LISTING_FILTERS,
  filterListings,
  sortListings,
  visibleInInventory,
  type ListingSortId,
  type ListingStatusFilter,
} from "./inventoryFilters";
import { activeAnchorMs, settledAnchorMs, type Listing } from "./sidebar/listingShelves";

export type MarketplaceCellKind = "listed" | "sold" | "draft" | "needs" | "failed" | "working" | "delisted" | "not_listed";

export interface MarketplaceCell {
  kind: MarketplaceCellKind;
  label: string;
  /** The live listing's page, when one is known. */
  url: string | null;
}

export type TableListing = {
  vendoo_marketplaces?: string[];
  vendoo_listing_urls?: Record<string, string>;
  vendoo_sold_dates?: Record<string, string>;
  direct_records?: Record<string, DirectRecord>;
  removed_marketplaces?: string[];
};

const LIVE = new Set(["published", "pending_review"]);
const BUSY = new Set(["saving", "publishing", "updating"]);
// Vendoo keys Vestiaire's API integration separately; Settings treats it as one marketplace.
const ALIASES: Record<string, string> = { vestiaireApi: "vestiaire" };

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

/**
 * Anteroom's own record outranks the imported history: it is newer, and a
 * direct delist is never reported back to the import. A sale stands either way.
 */
export function marketplaceCell(listing: TableListing, marketplace: string): MarketplaceCell {
  const record = listing.direct_records?.[marketplace];
  const imported = (listing.vendoo_marketplaces || []).some((id) => (ALIASES[id] || id) === marketplace);
  const removed = (listing.removed_marketplaces || []).some((id) => (ALIASES[id] || id) === marketplace);
  const url = record?.listing_url || listing.vendoo_listing_urls?.[marketplace] || null;
  if (listing.vendoo_sold_dates?.[marketplace]) return { kind: "sold", label: "Sold", url };
  if (record && LIVE.has(record.state)) {
    return { kind: "listed", label: record.state === "pending_review" ? "In review" : "Listed", url };
  }
  if (record && BUSY.has(record.state)) return { kind: "working", label: "Working", url: null };
  if (imported && !removed) return { kind: "listed", label: "Listed", url };
  if (record?.failed) {
    return record.fields
      ? { kind: "needs", label: `Needs ${plural(record.fields, "field")}`, url: null }
      : { kind: "failed", label: "Failed", url: null };
  }
  if (record?.state === "draft") return { kind: "draft", label: "Draft", url: null };
  if (record?.state === "unknown") return { kind: "working", label: "Checking", url: null };
  if (removed) return { kind: "delisted", label: "Delisted", url: null };
  return { kind: "not_listed", label: "Not listed", url: null };
}

/** How many listings are live on each marketplace, for the column headers. */
export function listedCounts(listings: TableListing[], marketplaces: string[]): Record<string, number> {
  const counts: Record<string, number> = Object.fromEntries(marketplaces.map((id) => [id, 0]));
  for (const listing of listings) {
    for (const id of marketplaces) {
      if (marketplaceCell(listing, id).kind === "listed") counts[id] += 1;
    }
  }
  return counts;
}

/**
 * The table's rows, filtered and ordered as the sidebar orders them: working
 * listings by recent activity, then finished ones by their sale. Finished
 * listings stay out unless searching or on Sold.
 */
export function inventoryRows<T extends Listing>(
  listings: readonly T[],
  needle: string,
  status: ListingStatusFilter,
  sort: ListingSortId,
): T[] {
  const visible = filterListings([...listings], needle, { ...DEFAULT_LISTING_FILTERS, status, sort })
    .filter((listing) => visibleInInventory(listing, status, Boolean(needle)));
  const active = visible.filter((listing) => !listing.settled_at).sort((left, right) => activeAnchorMs(right) - activeAnchorMs(left));
  const settled = visible.filter((listing) => listing.settled_at)
    .sort((left, right) => settledAnchorMs(right) - settledAnchorMs(left) || left.id.localeCompare(right.id));
  return sortListings([...active, ...settled], sort);
}
