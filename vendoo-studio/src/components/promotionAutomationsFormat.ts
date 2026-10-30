import type {
  PromotionAction, PromotionAutomation, PromotionAutomationBody, PromotionMarketplace, ShareOrder, ShareSpeed,
} from "../api/promotions";
import { browserTimezone } from "./automationsFormat";
import { marketplaceName } from "./marketplaceNames";

export interface PromotionAutomationForm {
  id: string | null;
  marketplace: PromotionMarketplace;
  action: PromotionAction;
  name: string;
  scope: "all" | "items";
  conversationIds: string[];
  excludedIds: string[];
  minDays: string;
  order: ShareOrder;
  speed: ShareSpeed;
  dailyCap: string;
  times: string;
  timezone: string;
  discount: string;
  minimumPrice: string;
  minLikes: string;
  shippingLabel: string;
}

export const SPEED_LABELS: Record<ShareSpeed, string> = { slow: "Slow (about 15 s apart)", normal: "Normal (about 7 s apart)", fast: "Fast (about 4 s apart)" };
export const ORDER_LABELS: Record<ShareOrder, string> = { newest: "Newest listed first", oldest: "Oldest listed first", random: "Random" };

export function emptyPromotionForm(action: PromotionAction, marketplace: PromotionMarketplace = "poshmark", timezone = browserTimezone()): PromotionAutomationForm {
  return { id: null, marketplace, action, name: "", scope: "all", conversationIds: [], excludedIds: [], minDays: "0", order: "newest",
    speed: "normal", dailyCap: "300", times: "09:00, 20:00", timezone, discount: "10", minimumPrice: "", minLikes: "1", shippingLabel: "" };
}

export function formFromAutomation(row: PromotionAutomation): PromotionAutomationForm {
  return { ...emptyPromotionForm(row.action, row.marketplace, row.timezone || browserTimezone()), id: row.id, name: row.name, scope: row.scope,
    conversationIds: row.conversation_ids, excludedIds: row.excluded_ids, minDays: String(row.min_days_listed),
    order: row.share_order || "newest", speed: row.speed || "normal", dailyCap: String(row.daily_cap ?? 300),
    times: row.times.join(", "), discount: String(row.discount_percent ?? 10), minLikes: String(row.min_likes ?? 1),
    shippingLabel: row.shipping_label || "", minimumPrice: row.minimum_price ?? "" };
}

function whole(value: string, low: number, high: number): number | null {
  if (!/^\d+$/.test(value.trim())) return null;
  const number = Number(value);
  return number >= low && number <= high ? number : null;
}

/** The request body, or what the seller still has to fix. */
export function bodyFromPromotionForm(form: PromotionAutomationForm, maxDailyCap: number): { body: PromotionAutomationBody } | { error: string } {
  const minDays = whole(form.minDays, 0, 365);
  if (minDays === null) return { error: "Minimum days listed is a whole number from 0 to 365." };
  if (form.scope === "items" && !form.conversationIds.length) return { error: "Choose at least one listing, or cover all live listings." };
  const base: PromotionAutomationBody = { marketplace: form.marketplace, action: form.action, name: form.name.trim(), scope: form.scope,
    conversation_ids: form.scope === "items" ? form.conversationIds : [], excluded_ids: form.excludedIds, min_days_listed: minDays };
  if (form.action === "share") {
    const cap = whole(form.dailyCap, 1, maxDailyCap);
    if (cap === null) return { error: `The daily cap is 1 to ${maxDailyCap.toLocaleString()} shares.` };
    const times = [...new Set(form.times.split(/[,\s]+/).filter(Boolean))];
    if (!times.length || times.length > 6 || times.some((time) => !/^([01]?\d|2[0-3]):[0-5]\d$/.test(time))) {
      return { error: "Enter 1 to 6 round times like 09:00, 20:00." };
    }
    return { body: { ...base, share_order: form.order, speed: form.speed, daily_cap: cap, times, timezone: form.timezone.trim() || "UTC" } };
  }
  const discount = whole(form.discount, 1, 99);
  if (discount === null) return { error: "Enter the discount as a whole percent from 1 to 99." };
  const likes = whole(form.minLikes, 1, 1000);
  if (likes === null) return { error: "Minimum likes is a whole number from 1 to 1,000." };
  const minimum = form.minimumPrice.trim();
  if (minimum && (!/^\d+(\.\d{1,2})?$/.test(minimum) || Number(minimum) <= 0 || Number(minimum) > 9999999.99)) {
    return { error: "Enter a positive minimum price with at most two decimal places, or leave it blank." };
  }
  return { body: { ...base, discount_percent: discount, minimum_price: minimum || null, min_likes: likes, shipping_label: form.marketplace === "poshmark" ? form.shippingLabel.trim() || null : null } };
}

/** Include or leave out one listing, keeping the scope the seller chose. */
export function withListing(form: PromotionAutomationForm, conversationId: string, include: boolean): PromotionAutomationForm {
  const drop = (ids: string[]) => ids.filter((id) => id !== conversationId);
  if (form.scope === "items") {
    return { ...form, conversationIds: include ? [...drop(form.conversationIds), conversationId] : drop(form.conversationIds) };
  }
  return { ...form, excludedIds: include ? drop(form.excludedIds) : [...drop(form.excludedIds), conversationId] };
}

export function describePromotionAutomation(row: PromotionAutomation): string {
  const scope = row.scope === "all" ? "all live listings" : `${row.conversation_ids.length} chosen ${row.conversation_ids.length === 1 ? "listing" : "listings"}`;
  const excluded = row.excluded_ids.length ? `, ${row.excluded_ids.length} excluded` : "";
  const age = row.min_days_listed ? `, listed ${row.min_days_listed}+ days` : "";
  if (row.action === "share") {
    return `Shares ${scope}${excluded}${age} at ${row.times.join(", ")} (${row.timezone}) · ${ORDER_LABELS[row.share_order || "newest"].toLowerCase()} · `
      + `${(row.speed || "normal")} · up to ${(row.daily_cap ?? 0).toLocaleString()} a day`;
  }
  const shipping = row.marketplace === "poshmark" ? ` · ${row.shipping_label ? `shipping discount “${row.shipping_label}”` : "no shipping discount"}` : "";
  return `${row.discount_percent}% off to new likers on ${scope}${excluded}${age} with ${row.min_likes}+ ${row.min_likes === 1 ? "like" : "likes"}`
    + ` · ${describePromotionMinimum(row)}${shipping} · ${describeOfferChecks(row.marketplace)} on ${marketplaceName(row.marketplace)}`;
}

export function describeOfferChecks(marketplace: PromotionMarketplace): string {
  return marketplace === "poshmark" || marketplace === "depop"
    ? "checks recent likes about every minute; catch-up checks every 15 minutes"
    : "checks every 15 minutes";
}

export function describePromotionMinimum(row: PromotionAutomation): string {
  return `${row.minimum_price ? `minimum $${row.minimum_price}` : "no shared minimum price"}; saved individual minimums also apply`;
}
