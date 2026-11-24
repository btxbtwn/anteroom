import type { BoxTotals } from "../api/types";
import { formatMoney } from "./analyticsFormat";

export const BOXES_QUERY_KEY = ["boxes"];

const STORE_NAMES: Record<string, string> = { raghouse: "Raghouse", tvf: "Thrift Vintage Fashion", papercrane: "PaperCrane" };

export function boxStoreName(store: string): string {
  return STORE_NAMES[store] ?? store;
}

/** A typed store name, with known wholesale stores saved under their canonical keys. */
export function boxStoreKey(name: string): string {
  const text = name.trim();
  const match = Object.entries(STORE_NAMES).find(([, label]) => label.toLowerCase() === text.toLowerCase());
  return match ? match[0] : text;
}

export function formatPercent(value: number | null): string {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

/** "Sold 4 of 20 · 9 listed" — what has happened to a box's sellable pieces so far. */
export function boxProgress(box: { sold: number; listed: number; listings: number; sellable?: number | null }): string {
  const of = box.sellable || box.listings;
  const parts = [of ? `Sold ${box.sold} of ${of}` : `Sold ${box.sold}`];
  if (box.listed > box.sold) parts.push(`${box.listed - box.sold} listed`);
  const drafts = box.listings - box.listed;
  if (drafts > 0) parts.push(drafts === 1 ? "1 draft" : `${drafts} drafts`);
  return parts.join(" · ");
}

/** The lowest price of the premium band; matches PREMIUM_PRICE in inventory_analytics.py. */
const PREMIUM_PRICE = 35;

/**
 * How a box or store grades as a supplier: average sale, premium sales, how
 * fast its pieces sold after buying, and how much arrived unsellable.
 */
export function boxScorecard(totals: BoxTotals): string[] {
  const parts: string[] = [];
  if (totals.asp != null) {
    parts.push(`${formatMoney(Math.round(totals.asp))} average sale`);
    parts.push(`${totals.sold_premium} of ${totals.sold} at ${formatMoney(PREMIUM_PRICE)}+`);
  }
  const windows = totals.sell_through_by_age.filter((window) => window.rate != null);
  if (windows.length) {
    parts.push(`sold ${windows.map((window) => `${formatPercent(window.rate)} by day ${window.days}`).join(", ")}`);
  }
  if (totals.unsellable_rate != null) parts.push(`${formatPercent(totals.unsellable_rate)} unsellable`);
  return parts;
}

/** "+$42 per $100 spent": profit for every $100 put into the box, or null before any money went in. */
export function profitPerHundred(roi: number | null): string | null {
  if (roi == null) return null;
  const per = Math.round(roi * 100);
  return `${per >= 0 ? "+" : "−"}${formatMoney(Math.abs(per))} per $100 spent`;
}
