const SETTLED_SHELF_KEY = "vendoo-studio.settled-expanded";

export type Listing = {
  id: string;
  title?: string | null;
  status?: string | null;
  settled_at?: string | null;
  unsettled_at?: string | null;
  created_at?: string;
  updated_at?: string;
  cover_photo_url?: string | null;
  vendoo_cover_url?: string | null;
  sku?: string | null;
  price?: number | null;
  vendoo_labels?: string[];
  vendoo_listed_at?: string | null;
  vendoo_sold_at?: string | null;
  vendoo_marketplaces?: string[];
  direct_marketplaces?: string[];
  removed_marketplaces?: string[];
  units_sold?: number;
  quantity?: number | null;
  vendoo_listed_dates?: Record<string, string>;
  vendoo_sold_dates?: Record<string, string>;
  vendoo_form_updated_at?: string | null;
  vendoo_relist_pending?: string[];
  unsent_edits?: boolean;
  etsy_eligible?: boolean;
};

export function readLegacySettledExpanded(): boolean | null {
  try {
    const raw = localStorage.getItem(SETTLED_SHELF_KEY);
    if (raw === null) return null;
    return raw === "true";
  } catch {
    return null;
  }
}

export function clearLegacySettledExpanded() {
  try {
    localStorage.removeItem(SETTLED_SHELF_KEY);
  } catch {
    /* ignore quota / private-mode failures */
  }
}

function timestampMs(value?: string | null): number {
  if (!value) return 0;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
}

export function activeAnchorMs(listing: Listing): number {
  return Math.max(timestampMs(listing.updated_at), timestampMs(listing.unsettled_at), timestampMs(listing.created_at));
}

export function settledAnchorMs(listing: Listing): number {
  return timestampMs(listing.settled_at) || timestampMs(listing.updated_at);
}

export function compactRelativeTime(iso?: string | null, now = Date.now()): string {
  const then = timestampMs(iso);
  if (!then) return "";
  const sec = Math.max(0, Math.round((now - then) / 1000));
  if (sec < 45) return "now";
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h`;
  const day = Math.round(hr / 24);
  if (day < 7) return `${day}d`;
  const week = Math.round(day / 7);
  if (week < 5) return `${week}w`;
  const month = Math.round(day / 30);
  if (month < 12) return `${month}mo`;
  return `${Math.round(day / 365)}y`;
}
