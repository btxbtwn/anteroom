import { liveStatusClass, liveStatusForMarketplace, mergeStatusDrafts } from "../fillLog/marketplaces";
import { marketplaceName } from "../marketplaceNames";

export { liveStatusClass };

// Vendoo's Inventory labels, plus the states that are Studio's own doing.
const STATUS_HINTS: Record<string, string> = {
  draft: "Draft in Vendoo",
  active: "Listed on a marketplace, per Vendoo",
  sold: "Sold, per Vendoo",
  failed: "The last send to Vendoo failed",
  in_progress: "Anteroom is working on this listing",
  listing: "Anteroom is sending this listing to Vendoo",
};
const MARKETPLACE_STATUS_ORDER = [
  "general",
  "ebay",
  "etsy",
  "poshmark",
  "mercari",
  "depop",
  "facebook",
  "shopify",
  "vinted",
  "whatnot",
  "grailed",
  "vestiaire",
  "sellwild",
];
// Vendoo keys Vestiaire's API integration separately; Settings treats it as one marketplace.
const MARKETPLACE_ID_ALIASES: Record<string, string> = { vestiaireApi: "vestiaire" };

/** Why a listing reads the way it does. Status follows Vendoo; it is never set here. */
export function statusHint(status: string): string {
  const hint = STATUS_HINTS[status];
  if (hint) return hint;
  return `${status.replace(/_/g, " ")} · follows Vendoo`;
}

function marketplaceLabel(id: string): string {
  return marketplaceName(id);
}

/** Listing status per marketplace, limited to `visible` (Settings → Listings → Marketplaces) when given. */
export function marketplaceStatusesFromDraft(
  draft: unknown,
  visible?: Set<string>,
): {
  id: string;
  label: string;
  status: string;
}[] {
  const merged = mergeStatusDrafts(draft);
  if (!merged) return [];
  const scraped = merged.statuses || {};
  const listings = merged.listings || {};
  const externalListings = merged.externalListings || {};
  const extraIds = [
    ...Object.keys(listings),
    ...Object.keys(externalListings),
  ].filter((id) => id !== "validate" && !MARKETPLACE_STATUS_ORDER.includes(id) && !(id in scraped));
  const ids = [
    ...MARKETPLACE_STATUS_ORDER,
    ...Object.keys(scraped).filter((id) => !MARKETPLACE_STATUS_ORDER.includes(id)),
    ...extraIds,
  ];

  const item = {
    statuses: scraped,
    listings,
    externalListings,
  };
  const rows: { id: string; label: string; status: string }[] = [];
  const seen = new Set<string>();
  for (const rawId of ids) {
    const id = MARKETPLACE_ID_ALIASES[rawId] || rawId;
    if (seen.has(id)) continue;
    if (visible && id !== "general" && !visible.has(id)) continue;
    const status = liveStatusForMarketplace(id, item);
    if (!status) continue;
    seen.add(id);
    rows.push({ id, label: marketplaceLabel(id), status });
  }
  return rows;
}

/** Where the import says the listing is live, for rows whose cached draft holds
 *  no per-marketplace status.
 *
 * `vendoo_marketplaces` is written from the same Vendoo payload the draft comes
 * from (`vendoo_listed_marketplaces`), counting sold as live, so an imported
 * listing can badge without anyone hovering it or pulling it again.
 */
export function listedFromImport(
  listing: {
    vendoo_marketplaces?: string[];
    vendoo_sold_dates?: Record<string, string>;
  },
  visible?: Set<string>,
): { id: string; label: string; status: string }[] {
  const sold = listing.vendoo_sold_dates || {};
  const rows: { id: string; label: string; status: string }[] = [];
  const seen = new Set<string>();
  for (const rawId of listing.vendoo_marketplaces || []) {
    const id = MARKETPLACE_ID_ALIASES[rawId] || rawId;
    if (id === "general" || seen.has(id)) continue;
    if (visible && !visible.has(id)) continue;
    seen.add(id);
    rows.push({
      id,
      label: marketplaceLabel(id),
      status: sold[rawId] || sold[id] ? "SOLD" : "LISTED",
    });
  }
  // The sidebar's own order, so a badged row reads the same whichever source
  // filled it.
  return rows.sort((left, right) => statusOrderIndex(left.id) - statusOrderIndex(right.id));
}

function statusOrderIndex(id: string): number {
  const index = MARKETPLACE_STATUS_ORDER.indexOf(id);
  return index === -1 ? MARKETPLACE_STATUS_ORDER.length : index;
}
