/**
 * One row shape for every automation, whichever backend owns it: scheduled
 * tasks (api/automations), eBay offers (api/ebayOffers) and sharing and offers
 * to likers (api/promotions). The page lists them together; each keeps its own
 * API and its own rules for turning on.
 */
import type { Automation, AutomationAction } from "../api/automations";
import type { EbayOffersStatus } from "../api/ebayOffers";
import type { PromotionAutomation } from "../api/promotions";
import { browserTimezone, describeActions, describeTrigger, formatRunTime } from "./automationsFormat";
import { marketplaceName } from "./marketplaceNames";

/** What New automation offers, in the order the type picker lists them. */
export type AutomationKind = "scheduled" | "ebay_offers" | "share" | "offer";

export const AUTOMATION_KINDS: readonly { kind: AutomationKind; label: string; description: string }[] = [
  { kind: "scheduled", label: "Price drop, relist or regenerate", description: "On a schedule; each item waits for your approval." },
  { kind: "ebay_offers", label: "eBay offers", description: "Offers to buyers watching your eBay listings." },
  { kind: "share", label: "Poshmark sharing", description: "Shares your Poshmark closet at the times you pick." },
  { kind: "offer", label: "Offers to likers", description: "Offers to new likers on Poshmark, Mercari or Depop." },
];

export interface AutomationRowView {
  key: string;
  kind: AutomationKind;
  name: string;
  type: string;
  when: string;
  /** What it does, when the type alone doesn't say (amounts, floors). */
  detail: string;
  lastRun: string;
  nextRun: string;
  /** The switch position, or null when the row shows `state` instead. */
  on: boolean | null;
  state: string;
  /** Why the switch can't be turned on, shown under the row. */
  note: string;
}

const ACTION_LABELS: Record<AutomationAction["type"], string> = {
  price: "Price drop", relist: "Relist", delist: "Delist", regenerate: "Regenerate", update_fields: "Update fields",
};

export function scheduledType(actions: readonly AutomationAction[]): string {
  const labels = [...new Set(actions.map((action) => ACTION_LABELS[action.type] ?? action.type))];
  return labels.join(", ") || "Scheduled";
}

const STATUS_WORD: Record<string, string> = { draft: "Draft", completed: "Done" };

export function scheduledRow(task: Automation): AutomationRowView {
  const togglable = task.status === "active" || task.status === "paused";
  return {
    key: `scheduled:${task.id}`,
    kind: "scheduled",
    name: task.name,
    type: scheduledType(task.actions),
    when: describeTrigger(task),
    // The type already says "Relist" or "Regenerate"; only amounts and floors add anything.
    detail: task.actions.some((action) => action.type === "price") ? describeActions(task.actions) : "",
    lastRun: formatRunTime(task.last_run_at, task.timezone),
    nextRun: task.status === "active" ? formatRunTime(task.next_run_at, task.timezone) : "—",
    on: togglable ? task.status === "active" : null,
    state: togglable ? "" : STATUS_WORD[task.status] ?? task.status,
    note: "",
  };
}

/** eBay offers is one setup per account: all listings, or the listings you picked. */
export function ebayOffersRow(status: EbayOffersStatus, timezone = browserTimezone()): AutomationRowView {
  const picked = status.listings.filter((item) => item.enabled);
  const all = status.allListings;
  const on = all.enabled || picked.length > 0;
  const scope = all.enabled
    ? `All listings, ${all.discountPercent}% off${all.minimumPrice ? `, minimum $${all.minimumPrice}` : ""}`
    : picked.length ? `${picked.length} listing${picked.length === 1 ? "" : "s"}` : "No listings yet";
  const every = `Every ${status.checkIntervalMinutes || 15} minutes`;
  const next = on && status.connected && status.lastCheck
    ? new Date(new Date(status.lastCheck).getTime() + (status.checkIntervalMinutes || 15) * 60_000).toISOString()
    : null;
  return {
    key: "ebay_offers",
    kind: "ebay_offers",
    name: "eBay offers",
    type: "eBay offers",
    when: `${every} · ${scope}`,
    detail: "",
    lastRun: formatRunTime(status.lastCheck, timezone),
    nextRun: on && status.connected ? (next ? formatRunTime(next, timezone) : `Within ${status.checkIntervalMinutes || 15} min`) : "—",
    on: status.connected ? on : null,
    state: status.connected ? "" : "Not connected",
    note: status.lastError || "",
  };
}

function clock(time: string): string {
  const [hours, minutes] = time.split(":").map(Number);
  return `${hours % 12 || 12}:${String(minutes || 0).padStart(2, "0")} ${hours < 12 ? "AM" : "PM"}`;
}

export function promotionType(row: Pick<PromotionAutomation, "action" | "marketplace">): string {
  return row.action === "share" ? `${marketplaceName(row.marketplace)} sharing` : "Offers to likers";
}

export function promotionRow(row: PromotionAutomation): AutomationRowView {
  const timezone = row.timezone || browserTimezone();
  const scope = row.scope === "all" ? "all live listings" : `${row.conversation_ids.length} chosen listing${row.conversation_ids.length === 1 ? "" : "s"}`;
  const when = row.action === "share"
    ? `Every day ${row.times.map(clock).join(", ")} · ${scope}`
    : `New likers on ${marketplaceName(row.marketplace)} · ${row.discount_percent}% off ${scope}`;
  return {
    key: `promotion:${row.id}`,
    kind: row.action,
    name: row.name,
    type: promotionType(row),
    when,
    detail: "",
    lastRun: formatRunTime(row.last_run?.started_at, timezone),
    nextRun: row.enabled ? formatRunTime(row.next_run_at, timezone) : "—",
    on: row.enabled,
    state: "",
    note: row.enabled ? "" : row.paused_reason || (row.available ? "" : `${marketplaceName(row.marketplace)} offers are not available in Anteroom yet`),
  };
}
