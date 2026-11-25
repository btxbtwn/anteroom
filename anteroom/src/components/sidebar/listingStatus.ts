import { statusIsListed } from "../fillLog/marketplaces";
import { marketplaceName } from "../marketplaceNames";
import { formatMoney } from "../analyticsFormat";

;

const STATUS_HINTS: Record<string, string> = {
  draft: "Not listed on any marketplace",
  inactive: "Delisted from every marketplace",
  active: "Listed on a marketplace",
  sold: "Sold",
  failed: "The last send failed",
  in_progress: "Anteroom is working on this listing",
  listing: "Anteroom is sending this listing",
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
  "whatnot",
  "vestiaire",
];
// The marketplaces Anteroom can delist. Vendoo's import also names others it
// cannot reach (Sellwild, Facebook); once the seller has delisted from Anteroom,
// those leftovers do not keep the item live.
const DELISTABLE_MARKETPLACES = new Set(["ebay", "etsy", "poshmark", "mercari", "depop"]);
/** Vendoo history can name these; Anteroom cannot list or delist them, so the inventory never shows them. */
const HIDDEN_IMPORTED_MARKETPLACES = new Set(["sellwild"]);
// Vendoo keys Vestiaire's API integration separately; Settings treats it as one marketplace.
const MARKETPLACE_ID_ALIASES: Record<string, string> = { vestiaireApi: "vestiaire" };

/** True when the inventory should never badge or filter by this imported marketplace. */
export function isHiddenImportedMarketplace(id: string): boolean {
  return HIDDEN_IMPORTED_MARKETPLACES.has(MARKETPLACE_ID_ALIASES[id] || id);
}

/** Why a listing reads the way it does. */
export function statusHint(status: string): string {
  const hint = STATUS_HINTS[status];
  if (hint) return hint;
  const plain = status.replace(/_/g, " ");
  return plain;
}

function marketplaceLabel(id: string): string {
  return marketplaceName(id);
}

/** Where the listing is live: Vendoo's word plus Anteroom's own publishes, less what Anteroom delisted. */
export function liveMarketplaces(listing: {
  vendoo_marketplaces?: string[];
  direct_marketplaces?: string[];
  removed_marketplaces?: string[];
  sold_marketplaces?: string[];
}): string[] {
  const removed = new Set((listing.removed_marketplaces || []).map((id) => MARKETPLACE_ID_ALIASES[id] || id));
  // A listing that sold has ended, though its record can still read live.
  const sold = new Set((listing.sold_marketplaces || []).map((id) => MARKETPLACE_ID_ALIASES[id] || id));
  const vendoo = (listing.vendoo_marketplaces || []).filter((id) => !removed.has(MARKETPLACE_ID_ALIASES[id] || id));
  return [...new Set([...vendoo, ...(listing.direct_marketplaces || [])])]
    .filter((id) => !sold.has(MARKETPLACE_ID_ALIASES[id] || id));
}

/** True when the listing is currently live on at least one marketplace. */
export function hasLiveListing(listing: {
  vendoo_marketplaces?: string[];
  direct_marketplaces?: string[];
  removed_marketplaces?: string[];
  sold_marketplaces?: string[];
}): boolean {
  const live = liveMarketplaces(listing);
  if (!live.length) return false;
  if ((listing.removed_marketplaces || []).length && !live.some((id) => DELISTABLE_MARKETPLACES.has(id))) {
    return false;
  }
  return true;
}

/**
 * The inventory label. Vendoo can still say draft after Anteroom published the
 * item itself; that row reads Active, the same as any other live listing. Once
 * Anteroom has delisted it from every marketplace it can reach, the row reads
 * Inactive and sits under Drafts. A sale and a send in progress keep their own
 * labels; an earlier failed Vendoo send cannot hide a listing now live from
 * Anteroom.
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
  if (
    (status === "active" || status === "draft")
    && (listing.removed_marketplaces || []).length
    && !liveMarketplaces(listing).some((id) => DELISTABLE_MARKETPLACES.has(id))
  ) return "inactive";
  return status;
}

/** Marketplace chips, with Anteroom's own live listings and delists outranking Vendoo's word. */
export function withDirectListings(
  rows: { id: string; label: string; status: string }[],
  direct: string[] | undefined,
  visible?: Set<string>,
  removed?: string[],
  sold?: string[],
): { id: string; label: string; status: string }[] {
  const gone = new Set((removed || []).map((id) => MARKETPLACE_ID_ALIASES[id] || id));
  // Where Anteroom recorded the sale. The listing record can still read live
  // (a sale never changes it), so the sale wins over it.
  const soldHere = new Set((sold || []).map((id) => MARKETPLACE_ID_ALIASES[id] || id)
    .filter((id) => !visible || visible.has(id)));
  // Vendoo is not told about a direct delist, so its "listed" is stale there.
  // A sale still stands.
  const kept = gone.size
    ? rows.map((row) => (gone.has(row.id) && row.status !== "SOLD" && row.status !== "SHIPPED"
      ? { ...row, status: "DELISTED" }
      : row))
    : rows;
  if (!direct?.length && !soldHere.size) return kept;
  const byId = new Map(kept.map((row) => [row.id, row]));
  let changed = false;
  for (const id of soldHere) {
    const existing = byId.get(id);
    if (existing?.status === "SOLD" || existing?.status === "SHIPPED") continue;
    byId.set(id, { ...(existing ?? { id, label: marketplaceLabel(id) }), status: "SOLD" });
    changed = true;
  }
  for (const rawId of direct ?? []) {
    const id = MARKETPLACE_ID_ALIASES[rawId] || rawId;
    if (!id || id === "general" || id === "validate" || soldHere.has(id)) continue;
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

/** Where the import recorded the listing as live, counting sold as live. */
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
    if (id === "general" || seen.has(id) || isHiddenImportedMarketplace(id)) continue;
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

/** "1 of 3 sold" for an Active item with some units sold; null otherwise. */
export function partialSale(listing: { units_sold?: number; quantity?: number | null }, status: string): string | null {
  const sold = listing.units_sold ?? 0;
  const quantity = listing.quantity ?? 0;
  return status === "active" && sold > 0 && quantity > sold ? `${sold} of ${quantity} sold` : null;
}

/** Units on hand for an unsold multi-quantity item. Once one sells, partialSale says it. */
export function quantityLabel(listing: { units_sold?: number; quantity?: number | null }, status: string): string | null {
  const quantity = listing.quantity ?? 0;
  return status !== "sold" && quantity > 1 && !(listing.units_sold ?? 0) ? `Qty ${quantity}` : null;
}

/** The asking price on a draft or active row. A sold item's asking price is not what it sold for. */
export function listingRowPrice(listing: { price?: number | null }, status: string): string | null {
  const price = listing.price;
  return status !== "sold" && price != null && Number.isFinite(price) && price > 0 ? formatMoney(price) : null;
}

/** What the row's marketplace logos say to a screen reader: live and sold apart. */
export function marketplacesLabel(rows: { label: string; status: string }[]): string {
  const sold = (status: string) => status === "SOLD" || status === "SHIPPED";
  const live = rows.filter((row) => !sold(row.status)).map((row) => row.label);
  const ended = rows.filter((row) => sold(row.status)).map((row) => row.label);
  return [live.length ? `Listed on ${live.join(", ")}` : "", ended.length ? `Sold on ${ended.join(", ")}` : ""]
    .filter(Boolean).join("; ");
}
