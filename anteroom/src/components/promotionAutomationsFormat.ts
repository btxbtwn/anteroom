import type {
  FollowConfig, HourlyIntensity, PromotionAction, PromotionAutomation, PromotionAutomationBody,
  PromotionMarketplace, ShareMode, ShareOrder, ShareSpeed, WaterfallStep,
} from "../api/promotions";
import { browserTimezone } from "./automationsFormat";
import { marketplaceName } from "./marketplaceNames";

export interface WaterfallStepDraft {
  discount: string;
  delayMinutes: string;
  shippingLabel: string;
}

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
  shareMode: ShareMode;
  useHourlySchedule: boolean;
  hourlySchedule: Record<string, HourlyIntensity>;
  discount: string;
  minimumPrice: string;
  minLikes: string;
  shippingLabel: string;
  waterfallSteps: WaterfallStepDraft[];
  bundlePauseMinutes: string;
  bundleMinItems: string;
  bundleComment: string;
  followReciprocal24h: boolean;
  followActiveSellersCap: string;
  unfollowCap: string;
  unsubscribeLiveShows: boolean;
}

export const SPEED_LABELS: Record<ShareSpeed, string> = {
  slow: "Slow (about 15 s apart)",
  normal: "Normal (about 7 s apart)",
  fast: "Fast (about 4 s apart)",
};
export const ORDER_LABELS: Record<ShareOrder, string> = {
  newest: "Newest listed first",
  oldest: "Oldest listed first",
  random: "Random",
};
export const SHARE_MODE_LABELS: Record<ShareMode, string> = {
  self: "Self shares (your closet)",
  party: "Posh Party shares (9a / 12p / 4p / 7p PT)",
  community: "Community shares (other closets)",
};
export const HOURLY_INTENSITY_LABELS: Record<HourlyIntensity, string> = {
  none: "Off",
  low: "Low",
  medium: "Medium",
  high: "High",
};

export function defaultHourlySchedule(): Record<string, HourlyIntensity> {
  const out: Record<string, HourlyIntensity> = {};
  for (let h = 0; h < 24; h++) {
    out[String(h)] = h >= 8 && h <= 21 ? "medium" : "none";
  }
  return out;
}

export function emptyPromotionForm(
  action: PromotionAction,
  marketplace: PromotionMarketplace = "poshmark",
  timezone = browserTimezone(),
): PromotionAutomationForm {
  return {
    id: null,
    marketplace,
    action,
    name: "",
    scope: "all",
    conversationIds: [],
    excludedIds: [],
    minDays: "0",
    order: "newest",
    speed: "normal",
    dailyCap: "300",
    times: "09:00, 20:00",
    timezone,
    shareMode: "self",
    useHourlySchedule: false,
    hourlySchedule: defaultHourlySchedule(),
    discount: "10",
    minimumPrice: "",
    minLikes: "1",
    shippingLabel: "",
    waterfallSteps: [],
    bundlePauseMinutes: "5",
    bundleMinItems: "1",
    bundleComment: "",
    followReciprocal24h: true,
    followActiveSellersCap: "250",
    unfollowCap: "0",
    unsubscribeLiveShows: false,
  };
}

export function formFromAutomation(row: PromotionAutomation): PromotionAutomationForm {
  const base = emptyPromotionForm(row.action, row.marketplace, row.timezone || browserTimezone());
  const follow: FollowConfig = row.follow_config || {};
  return {
    ...base,
    id: row.id,
    name: row.name,
    scope: row.scope,
    conversationIds: row.conversation_ids,
    excludedIds: row.excluded_ids,
    minDays: String(row.min_days_listed),
    order: row.share_order || "newest",
    speed: row.speed || "normal",
    dailyCap: String(row.daily_cap ?? 300),
    times: row.times.join(", "),
    shareMode: row.share_modes?.[0] || "self",
    useHourlySchedule: Boolean(row.hourly_schedule && Object.keys(row.hourly_schedule).length),
    hourlySchedule: row.hourly_schedule ? { ...defaultHourlySchedule(), ...row.hourly_schedule } : defaultHourlySchedule(),
    discount: String(row.discount_percent ?? 10),
    minLikes: String(row.min_likes ?? 1),
    shippingLabel: row.shipping_label || "",
    minimumPrice: row.minimum_price ?? "",
    waterfallSteps: (row.waterfall_steps || []).map((step) => ({
      discount: String(step.discount_percent),
      delayMinutes: String(step.delay_minutes),
      shippingLabel: step.shipping_label || "",
    })),
    bundlePauseMinutes: String(row.bundle_pause_minutes ?? 5),
    bundleMinItems: String(row.bundle_min_items ?? 1),
    bundleComment: row.bundle_comment ?? "",
    followReciprocal24h: Boolean(follow.reciprocal_24h),
    followActiveSellersCap: String(follow.new_active_sellers_cap ?? 0),
    unfollowCap: String(follow.unfollow_cap ?? 0),
    unsubscribeLiveShows: Boolean(follow.unsubscribe_live_shows),
  };
}

function whole(value: string, low: number, high: number): number | null {
  if (!/^\d+$/.test(value.trim())) return null;
  const number = Number(value);
  return number >= low && number <= high ? number : null;
}

function parseWaterfall(steps: WaterfallStepDraft[]): { steps: WaterfallStep[] } | { error: string } {
  const parsed: WaterfallStep[] = [];
  for (let i = 0; i < steps.length; i++) {
    const raw = steps[i]!;
    const discount = whole(raw.discount, 1, 99);
    if (discount === null) return { error: `Waterfall step ${i + 1}: enter a discount between 1% and 99%.` };
    const delay = whole(raw.delayMinutes, 0, 60 * 24 * 30);
    if (delay === null) return { error: `Waterfall step ${i + 1}: enter a non-negative delay in minutes.` };
    if (i > 0 && discount <= parsed[i - 1]!.discount_percent) {
      return { error: `Waterfall step ${i + 1}: discount must increase from step ${i} (${parsed[i - 1]!.discount_percent}%).` };
    }
    if (i > 0 && delay <= parsed[i - 1]!.delay_minutes) {
      return { error: `Waterfall step ${i + 1}: delay must be greater than step ${i}.` };
    }
    parsed.push({
      discount_percent: discount,
      delay_minutes: delay,
    });
  }
  return { steps: parsed };
}

/** The request body, or what the seller still has to fix. */
export function bodyFromPromotionForm(
  form: PromotionAutomationForm,
  maxDailyCap: number,
): { body: PromotionAutomationBody } | { error: string } {
  const minDays = whole(form.minDays, 0, 365);
  if (minDays === null) return { error: "Minimum days listed is a whole number from 0 to 365." };
  if (form.action !== "follow" && form.scope === "items" && !form.conversationIds.length) {
    return { error: "Choose at least one listing, or cover all live listings." };
  }
  const base: PromotionAutomationBody = {
    marketplace: form.marketplace,
    action: form.action,
    name: form.name.trim(),
    scope: form.action === "follow" ? "all" : form.scope,
    conversation_ids: form.scope === "items" && form.action !== "follow" ? form.conversationIds : [],
    excluded_ids: form.action === "follow" ? [] : form.excludedIds,
    min_days_listed: minDays,
  };

  if (form.action === "share") {
    const cap = whole(form.dailyCap, 1, maxDailyCap);
    if (cap === null) return { error: `The daily cap is 1 to ${maxDailyCap.toLocaleString()} shares.` };
    const times = [...new Set(form.times.split(/[,\s]+/).filter(Boolean))];
    if (!form.useHourlySchedule && (!times.length || times.length > 6 || times.some((time) => !/^([01]?\d|2[0-3]):[0-5]\d$/.test(time)))) {
      return { error: "Enter 1 to 6 round times like 09:00, 20:00, or enable the 24-hour intensity schedule." };
    }
    const shareBody: PromotionAutomationBody = {
      ...base,
      share_order: form.order,
      speed: form.speed,
      daily_cap: cap,
      times: times.length ? times : ["09:00"],
      timezone: form.timezone.trim() || "UTC",
    };
    if (form.shareMode !== "self") shareBody.share_modes = [form.shareMode];
    if (form.useHourlySchedule) shareBody.hourly_schedule = form.hourlySchedule;
    return { body: shareBody };
  }

  if (form.action === "follow") {
    const activeCap = whole(form.followActiveSellersCap, 0, 1000);
    if (activeCap === null) return { error: "Active seller follow cap must be 0 to 1,000 per day." };
    const unfollowCap = whole(form.unfollowCap, 0, 1000);
    if (unfollowCap === null) return { error: "Unfollow cap must be 0 to 1,000 per day." };
    if (!form.followReciprocal24h && activeCap === 0 && unfollowCap === 0 && !form.unsubscribeLiveShows) {
      return { error: "Enable reciprocal follows, active seller follows, unfollows, or Live Show unsubscribe." };
    }
    const times = [...new Set(form.times.split(/[,\s]+/).filter(Boolean))];
    if (!times.length || times.length > 6 || times.some((time) => !/^([01]?\d|2[0-3]):[0-5]\d$/.test(time))) {
      return { error: "Enter 1 to 6 round times like 09:00, 18:00." };
    }
    return {
      body: {
        ...base,
        marketplace: "poshmark",
        times,
        timezone: form.timezone.trim() || "UTC",
        follow_config: {
          reciprocal_24h: form.followReciprocal24h,
          new_active_sellers_cap: activeCap,
          unfollow_cap: unfollowCap,
          unsubscribe_live_shows: form.unsubscribeLiveShows,
        },
      },
    };
  }

  const discount = whole(form.discount, 1, 99);
  if (discount === null) return { error: "Enter the discount as a whole percent from 1 to 99." };
  const likes = whole(form.minLikes, 1, 1000);
  if (likes === null) return { error: "Minimum likes is a whole number from 1 to 1,000." };
  const minimum = form.minimumPrice.trim();
  if (minimum && (!/^\d+(\.\d{1,2})?$/.test(minimum) || Number(minimum) <= 0 || Number(minimum) > 9999999.99)) {
    return { error: "Enter a positive minimum price with at most two decimal places, or leave it blank." };
  }

  let waterfallSteps: WaterfallStep[] | null = null;
  if (form.waterfallSteps.length) {
    const parsed = parseWaterfall(form.waterfallSteps);
    if ("error" in parsed) return parsed;
    waterfallSteps = parsed.steps;
  }

  if (form.action === "bundle_offer") {
    const pauseMin = whole(form.bundlePauseMinutes, 1, 1440);
    if (pauseMin === null) return { error: "Like-activity pause must be between 1 and 1,440 minutes." };
    const minItems = whole(form.bundleMinItems, 1, 20);
    if (minItems === null) return { error: "Minimum bundle items must be between 1 and 20." };
    return {
      body: {
        ...base,
        marketplace: "poshmark",
        discount_percent: waterfallSteps ? waterfallSteps[0]!.discount_percent : discount,
        minimum_price: minimum || null,
        min_likes: likes,
        shipping_label: form.shippingLabel.trim() || null,
        waterfall_steps: waterfallSteps,
        bundle_pause_minutes: pauseMin,
        bundle_min_items: minItems,
        bundle_comment: form.bundleComment.trim() || null,
      },
    };
  }

  const offerBody: PromotionAutomationBody = {
    ...base,
    discount_percent: waterfallSteps ? waterfallSteps[0]!.discount_percent : discount,
    minimum_price: minimum || null,
    min_likes: likes,
    shipping_label: form.marketplace === "poshmark" ? form.shippingLabel.trim() || null : null,
  };
  if (waterfallSteps) offerBody.waterfall_steps = waterfallSteps;
  return { body: offerBody };
}

/** Include or leave out one listing, keeping the scope the seller chose. */
export function withListing(form: PromotionAutomationForm, conversationId: string, include: boolean): PromotionAutomationForm {
  const drop = (ids: string[]) => ids.filter((id) => id !== conversationId);
  if (form.scope === "items") {
    return { ...form, conversationIds: include ? [...drop(form.conversationIds), conversationId] : drop(form.conversationIds) };
  }
  return { ...form, excludedIds: include ? drop(form.excludedIds) : [...drop(form.excludedIds), conversationId] };
}

export function describeWaterfall(steps: readonly WaterfallStep[] | null | undefined): string {
  if (!steps || !steps.length) return "";
  return steps
    .map((step) => {
      const delay = step.delay_minutes >= 60 && step.delay_minutes % 60 === 0
        ? `${step.delay_minutes / 60}h`
        : `${step.delay_minutes}m`;
      return `${step.discount_percent}% after ${delay}`;
    })
    .join(" → ");
}

export function describePromotionAutomation(row: PromotionAutomation): string {
  const scope = row.scope === "all" ? "all live listings" : `${row.conversation_ids.length} chosen ${row.conversation_ids.length === 1 ? "listing" : "listings"}`;
  const excluded = row.excluded_ids.length ? `, ${row.excluded_ids.length} excluded` : "";
  const age = row.min_days_listed ? `, listed ${row.min_days_listed}+ days` : "";
  if (row.action === "share") {
    const mode = row.share_modes?.[0] || "self";
    const scheduleText = row.hourly_schedule ? "24-hour intensity schedule" : `at ${row.times.join(", ")}`;
    const prefix = mode === "self" ? "Shares" : `${SHARE_MODE_LABELS[mode]} ·`;
    return `${prefix} ${scope}${excluded}${age} ${scheduleText} (${row.timezone}) · ${ORDER_LABELS[row.share_order || "newest"].toLowerCase()} · `
      + `${(row.speed || "normal")} · up to ${(row.daily_cap ?? 0).toLocaleString()} a day`;
  }
  if (row.action === "follow") {
    const cfg = row.follow_config || {};
    const parts: string[] = [];
    if (cfg.reciprocal_24h) parts.push("reciprocal 24h");
    if (cfg.new_active_sellers_cap) parts.push(`up to ${cfg.new_active_sellers_cap} active sellers/day`);
    if (cfg.unfollow_cap) parts.push(`unfollow up to ${cfg.unfollow_cap}/day`);
    if (cfg.unsubscribe_live_shows) parts.push("unsubscribe Live Shows");
    return `Poshmark automatic follows at ${row.times.join(", ")} (${row.timezone}) · ${parts.join(" · ") || "default caps"}`;
  }
  const waterfallText = describeWaterfall(row.waterfall_steps);
  const discountText = waterfallText ? `Waterfall (${waterfallText})` : `${row.discount_percent}% off`;
  const shipping = row.marketplace === "poshmark" ? ` · ${row.shipping_label ? `shipping discount “${row.shipping_label}”` : "no shipping discount"}` : "";
  if (row.action === "bundle_offer") {
    return `Private bundle offers · ${discountText} on ${scope}${excluded}${age} · waits ${row.bundle_pause_minutes ?? 5}m after like activity · `
      + `${row.bundle_min_items ?? 1}+ items · ${describePromotionMinimum(row)}${shipping}`;
  }
  return `${discountText} to new likers on ${scope}${excluded}${age} with ${row.min_likes}+ ${row.min_likes === 1 ? "like" : "likes"}`
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
