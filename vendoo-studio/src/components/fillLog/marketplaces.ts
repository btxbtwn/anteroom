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
  if (
    key === "listed" || key === "live" || key === "active" || key === "published"
    || key === "complete" || key === "sold" || key === "shipped"
  ) return "is-listed";
  if (key === "not-listed" || key === "draft" || key === "incomplete" || key === "delisted" || key === "idle") {
    return "is-not-listed";
  }
  if (key === "failed" || key === "error") return "is-failed";
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
  if (row.shipped === true) return "SHIPPED";
  if (row.listed === true) return "LISTED";
  if (row.delisted === true) return "DELISTED";
  if (row.notListed === true || row.listed === false) return "NOT LISTED";
  if (row.incomplete === true) return "INCOMPLETE";
  if (row.failed === true || row.error === true) return "FAILED";
  if (row.pending === true) return "PENDING";
  return undefined;
}

function normalizeLiveStatus(raw: unknown): string | undefined {
  if (typeof raw !== "string") return undefined;
  const cleaned = raw.replace(/\s+/g, " ").trim().toUpperCase();
  if (!cleaned || cleaned === "BETA" || cleaned === "NEW" || cleaned === "ALPHA") return undefined;
  return cleaned;
}

/** How far along a marketplace listing is. A live listing outranks a nav label that still says not listed. */
function statusRank(status?: string): number {
  const key = (status || "").toUpperCase();
  if (key === "SOLD" || key === "SHIPPED") return 5;
  if (key === "LISTED" || key === "LIVE" || key === "ACTIVE" || key === "PUBLISHED") return 4;
  if (key === "LISTING" || key === "PENDING" || key === "DELISTING") return 3;
  if (!key) return 0;
  if (key === "NOT LISTED" || key === "DRAFT" || key === "INCOMPLETE" || key === "IDLE") return 1;
  return 2;
}

function preferStatus(current: string | undefined, next: string | undefined): string | undefined {
  if (!next) return current;
  if (!current || statusRank(next) > statusRank(current)) return next;
  return current;
}

/** True when the chip means the item is up on that marketplace, including sold. */
export function statusIsListed(status?: string): boolean {
  return statusRank(status) >= 4;
}

// Vendoo keys Vestiaire's API integration separately; the nav treats it as one marketplace.
const STATUS_ID_ALIASES: Record<string, string> = { vestiaireApi: "vestiaire" };

function marketplaceKeys(id: string): string[] {
  const keys = new Set<string>([id]);
  for (const [raw, alias] of Object.entries(STATUS_ID_ALIASES)) {
    if (id === raw || id === alias) {
      keys.add(raw);
      keys.add(alias);
    }
  }
  return [...keys];
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  return value as Record<string, unknown>;
}

function recordStatus(group: unknown, id: string): string | undefined {
  const listings = asRecord(group);
  if (!listings) return undefined;
  let best: string | undefined;
  for (const key of marketplaceKeys(id)) {
    best = preferStatus(best, statusFromListingStatus(asRecord(listings[key])?.status));
  }
  return best;
}

function scrapedStatus(statuses: unknown, id: string): string | undefined {
  const map = asRecord(statuses);
  if (!map) return undefined;
  let best: string | undefined;
  for (const key of marketplaceKeys(id)) best = preferStatus(best, normalizeLiveStatus(map[key]));
  return best;
}

export function liveStatusForMarketplace(
  id: string,
  item: Record<string, unknown> | null | undefined,
): string | undefined {
  if (!item) return undefined;
  const scraped = scrapedStatus(item.statuses, id);
  if (id === "general") return scraped;
  // `listings` is what Vendoo posted. `externalListings` is a listing the seller
  // already had on the marketplace. Either one being live is the real status;
  // a nav scrape that still says NOT LISTED does not hide it.
  return preferStatus(
    scraped,
    preferStatus(recordStatus(item.listings, id), recordStatus(item.externalListings, id)),
  );
}

export type StatusDraft = {
  statuses?: Record<string, unknown>;
  listings?: Record<string, unknown>;
  externalListings?: Record<string, unknown>;
};

function statusSlice(draft: unknown): StatusDraft | undefined {
  const root = asRecord(draft);
  if (!root) return undefined;
  const item = asRecord(root.item);
  const form = asRecord(root.form);
  const statuses = asRecord(root.statuses) || asRecord(form?.statuses) || asRecord(item?.statuses);
  const listings = asRecord(item?.listings) || asRecord(form?.listings) || asRecord(root.listings);
  const externalListings = asRecord(item?.externalListings)
    || asRecord(form?.externalListings)
    || asRecord(root.externalListings);
  if (!statuses && !listings && !externalListings) return undefined;
  return { statuses, listings, externalListings };
}

function mergeListingGroups(
  groups: Array<Record<string, unknown> | undefined>,
): Record<string, unknown> | undefined {
  const out: Record<string, unknown> = {};
  for (const group of groups) {
    if (!group) continue;
    for (const [id, listing] of Object.entries(group)) {
      if (id === "validate") continue;
      const row = asRecord(listing);
      const prev = asRecord(out[id]);
      if (!prev || statusRank(statusFromListingStatus(row?.status)) > statusRank(statusFromListingStatus(prev.status))) {
        out[id] = { status: row?.status };
      }
    }
  }
  return Object.keys(out).length ? out : undefined;
}

function mergeScraped(
  groups: Array<Record<string, unknown> | undefined>,
): Record<string, unknown> | undefined {
  const out: Record<string, unknown> = {};
  for (const group of groups) {
    if (!group) continue;
    for (const [id, raw] of Object.entries(group)) {
      const next = normalizeLiveStatus(raw);
      if (!next) continue;
      const prev = normalizeLiveStatus(out[id]);
      if (!prev || statusRank(next) > statusRank(prev)) out[id] = next;
    }
  }
  return Object.keys(out).length ? out : undefined;
}

/** One status picture from the fields-panel item and the sidebar cache. */
export function mergeStatusDrafts(...drafts: unknown[]): StatusDraft | undefined {
  const parts = drafts.map(statusSlice).filter((part): part is StatusDraft => Boolean(part));
  if (!parts.length) return undefined;
  const statuses = mergeScraped(parts.map((part) => part.statuses));
  const listings = mergeListingGroups(parts.map((part) => part.listings));
  const externalListings = mergeListingGroups(parts.map((part) => part.externalListings));
  if (!statuses && !listings && !externalListings) return undefined;
  return { statuses, listings, externalListings };
}
