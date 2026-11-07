import { filterListings, sortListings, visibleInInventory, type ListingFilters } from "../inventoryFilters";
import { activeAnchorMs, settledAnchorMs, type Listing } from "./listingShelves";

/**
 * The sidebar's rows for a search and filter set: open listings above
 * finished ones (unless another sort orders the whole list), finished ones
 * only while searching or on their own tab, and the listing that is open in
 * the editor kept on screen even when it is finished.
 */
export function shelveListings(
  listings: Listing[],
  needle: string,
  filters: ListingFilters,
  openListingId: string | null,
): { shown: Listing[]; matched: number } {
  const visible = filterListings(listings, needle, filters);
  const active = visible.filter((listing) => !listing.settled_at).sort((left, right) => activeAnchorMs(right) - activeAnchorMs(left));
  const settled = visible.filter((listing) => listing.settled_at).sort((left, right) => settledAnchorMs(right) - settledAnchorMs(left) || left.id.localeCompare(right.id));
  const ordered = sortListings([...active, ...settled], filters.sort);
  const shown = ordered.filter((listing) => visibleInInventory(listing, filters.status, Boolean(needle)));
  if (openListingId && !shown.some((listing) => listing.id === openListingId)) {
    const open = settled.find((listing) => listing.id === openListingId);
    if (open) shown.unshift(open);
  }
  return { shown, matched: ordered.length };
}
