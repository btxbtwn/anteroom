/** The one line the empty listing pane shows: what to do next. */
export function inventoryEmptyTitle(listingCount: number | undefined): string {
  return listingCount
    ? "Open a listing, or start a new one from product photos"
    : "Create your first listing from product photos";
}
