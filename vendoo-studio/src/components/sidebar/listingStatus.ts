import { liveStatusClass, liveStatusForMarketplace, mergeStatusDrafts, statusIsListed } from "../fillLog/marketplaces";
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

/** Why a listing reads the way it does. Status follows Vendoo, except a direct publish. */
export function statusHint(status: string): string {
  const hint = STATUS_HINTS[status];
  if (hint) return hint;
  return `${status.replace(/_/g, " ")} · follows Vendoo`;
}

function marketplaceLabel(id: string): string {
  return marketplaceName(id);
}

/** Where the listing is live: Vendoo's word plus Anteroom's own publishes, less what Anteroom delisted. */
export function liveMarketplaces(listing: {
  vendoo_marketplaces?: string[];
  direct_marketplaces?: string[];
  removed_marketplaces?: string[];
}): string[] {
  const removed = new Set((listing.removed_marketplaces || []).map((id) => MARKETPLACE_ID_ALIASES[id] || id));
  const vendoo = (listing.vendoo_marketplaces || []).filter((id) => !removed.has(MARKETPLACE_ID_ALIASES[id] || id));
  return [...new Set([...vendoo, ...(listing.direct_marketplaces || [])])];
}

/**
 * The inventory label. Vendoo can still say draft after Anteroom published the
 * item itself; that row reads Active, the same as any other live listing. It
 * can also still say active after Anteroom delisted the item everywhere; that
 * row reads Draft. A sale and a send in progress keep their own labels; an
 * earlier failed Vendoo send cannot hide a listing now live from Anteroom.
 */
export function inventoryStatus(listing: {
  status?: string | null;
  vendoo_marketplaces?: string[];
  direct_marketplaces?: string[];
  removed_marketplaces?: string[];
}): string {
  const status = String(listing.status || "draft");
  if (status === "sold" || status === "listing") return status;
  if ((listing.direct_marketplaces || []).length && ["draft", "in_progress", "failed"].includes(status)) return "active";
  if (status === "active" && (listing.removed_marketplaces || []).length && !liveMarketplaces(listing).length) return "draft";
  return status;
}

/** Marketplace chips, with Anteroom's own live listings and delists outranking Vendoo's word. */
export function withDirectListings(
  rows: { id: string; label: string; status: string }[],
  direct: string[] | undefined,
  visible?: Set<string>,
  removed?: string[],
): { id: string; label: string; status: string }[] {
  const gone = new Set((removed || []).map((id) => MARKETPLACE_ID_ALIASES[id] || id));
  // Vendoo is not told about a direct delist, so its "listed" is stale there.
  // A sale still stands.
  const kept = gone.size
    ? rows.map((row) => (gone.has(row.id) && row.status !== "SOLD" && row.status !== "SHIPPED"
      ? { ...row, status: "DELISTED" }
      : row))
    : rows;
  if (!direct?.length) return kept;
  const byId = new Map(kept.map((row) => [row.id, row]));
  let changed = false;
  for (const rawId of direct) {
    const id = MARKETPLACE_ID_ALIASES[rawId] || rawId;
    if (!id || id === "general" || id === "validate") continue;
    if (visible && !visible.has(id)) continue;
    const existing = byId.get(id);
    if (!existing) {
      byId.set(id, { id, label: marketplaceLabel(id), status: "LISTED" });
      changed = true;
    } else if (!statusIsListed(existing.status)) {
      byId.set(id, { ...existing, status: "LISTED" });
      changed = true;
    }
  }
  if (!changed) return kept;
  return [...byId.values()].sort((left, right) => statusOrderIndex(left.id) - statusOrderIndex(right.id));
}

/** Listing status per marketplace, limited to `visible` (Settings → Listings → Where you list) when given. */
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
