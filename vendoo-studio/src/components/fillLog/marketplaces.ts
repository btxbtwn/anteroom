const DEFAULT_SELECTED_MARKETPLACES = ["ebay", "etsy", "poshmark", "mercari", "depop"];
export const MARKETPLACE_ORDER = ["general", "ebay", "etsy", "poshmark", "mercari", "depop", "facebook", "shopify", "vinted", "whatnot", "sellwild", "grailed"];
const MARKETPLACE_LABELS: Record<string, string> = {
  general: "Vendoo",
  ebay: "eBay",
  etsy: "Etsy",
  poshmark: "Poshmark",
  mercari: "Mercari",
  depop: "Depop",
  facebook: "Facebook",
  shopify: "Shopify",
  vinted: "Vinted",
  whatnot: "Whatnot",
  sellwild: "Sellwild",
  grailed: "Grailed",
  vestiaire: "Vestiaire",
  kidizen: "Kidizen",
};

export function allowedMarketplaceIds(selected?: string[]): Set<string> {
  const chosen = selected ?? DEFAULT_SELECTED_MARKETPLACES;
  return new Set(["general", ...chosen]);
}

export function marketplaceLabel(id: string): string {
  return MARKETPLACE_LABELS[id] || id.charAt(0).toUpperCase() + id.slice(1);
}

export function liveStatusClass(status?: string): string {
  const key = (status || "").toLowerCase().replace(/\s+/g, "-");
  if (key === "listed" || key === "complete" || key === "sold") return "is-listed";
  if (key === "not-listed" || key === "draft" || key === "incomplete") return "is-not-listed";
  if (key === "failed") return "is-failed";
  return "";
}

/**
 * True when the item is (or was) actually on this marketplace. Every Vendoo
 * draft carries a "Not listed" status for every marketplace, so that alone is
 * no reason to show one the seller turned off in Settings.
 */
export function isLiveOnMarketplace(status?: string): boolean {
  return Boolean(status) && liveStatusClass(status) !== "is-not-listed";
}

/** Map Vendoo's API ``listings.<mp>.status`` object onto the nav chip labels. */
export function statusFromListingStatus(status: unknown): string | undefined {
  if (!status || typeof status !== "object" || Array.isArray(status)) return undefined;
  const row = status as Record<string, unknown>;
  // Real drafts from get_item use { notListed: true }, not { listed: false }.
  if (row.sold === true) return "SOLD";
  if (row.listed === true) return "LISTED";
  if (row.notListed === true || row.listed === false) return "NOT LISTED";
  if (row.incomplete === true) return "INCOMPLETE";
  if (row.failed === true) return "FAILED";
  if (row.pending === true) return "PENDING";
  return undefined;
}

function normalizeLiveStatus(raw: unknown): string | undefined {
  if (typeof raw !== "string") return undefined;
  const cleaned = raw.replace(/\s+/g, " ").trim().toUpperCase();
  if (!cleaned || cleaned === "BETA" || cleaned === "NEW" || cleaned === "ALPHA") return undefined;
  return cleaned;
}

export function liveStatusForMarketplace(
  id: string,
  item: Record<string, unknown> | null | undefined,
): string | undefined {
  const statuses = item?.statuses;
  if (statuses && typeof statuses === "object" && !Array.isArray(statuses)) {
    const scraped = normalizeLiveStatus((statuses as Record<string, unknown>)[id]);
    if (scraped) return scraped;
  }
  if (id === "general") return undefined;
  const listings = item?.listings && typeof item.listings === "object"
    ? item.listings as Record<string, unknown>
    : undefined;
  const listing = listings?.[id] as Record<string, unknown> | undefined;
  return statusFromListingStatus(listing?.status);
}
