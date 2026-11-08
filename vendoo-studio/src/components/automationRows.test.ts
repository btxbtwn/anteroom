import { describe, expect, it } from "vitest";

import type { Automation } from "../api/automations";
import type { EbayOffersStatus } from "../api/ebayOffers";
import type { PromotionAutomation } from "../api/promotions";
import { AUTOMATION_KINDS, ebayOffersRow, promotionRow, scheduledRow, scheduledType } from "./automationRows";

const task = (overrides: Partial<Automation> = {}): Automation => ({
  id: "a1", name: "Refresh stale listings", prompt: null, status: "active", timezone: "America/Chicago",
  schedule: { kind: "daily", time: "09:00" }, filters: { min_age_days: 60 },
  actions: [{ type: "price", mode: "percent", value: 10, floor: 15, repeat: "step" }, { type: "relist" }],
  marketplaces: ["ebay"], summary: "", confirmed_at: null,
  next_run_at: "2026-11-05T15:00:00+00:00", last_run_at: null, created_at: null, updated_at: null,
  ...overrides,
});

const ebay = (overrides: Partial<EbayOffersStatus> = {}): EbayOffersStatus => ({
  connected: true, allListings: { enabled: false, discountPercent: 10, minimumPrice: "" }, listings: [], history: [],
  checkIntervalMinutes: 15, lastCheck: null, lastError: null, ...overrides,
});

const promotion = (overrides: Partial<PromotionAutomation> = {}): PromotionAutomation => ({
  id: "p1", marketplace: "poshmark", action: "share", name: "Closet sharing", scope: "all", conversation_ids: [], excluded_ids: [],
  min_days_listed: 0, share_order: "newest", speed: "normal", daily_cap: 300, times: ["09:00", "20:30"], timezone: "UTC",
  discount_percent: null, minimum_price: null, min_likes: null, shipping_label: null, enabled: false, available: true,
  paused_reason: null, next_run_at: null, last_run: null, ...overrides,
});

describe("automation rows", () => {
  it("offers every kind in the type picker with a one-line description", () => {
    expect(AUTOMATION_KINDS.map((kind) => kind.label)).toEqual([
      "Price drop, relist or regenerate", "eBay offers", "Poshmark sharing", "Offers to likers",
    ]);
    for (const kind of AUTOMATION_KINDS) expect(kind.description).not.toContain("\n");
  });

  it("names a scheduled task by what it does", () => {
    expect(scheduledType([{ type: "price" }, { type: "relist" }, { type: "price" }])).toBe("Price drop, Relist");
    expect(scheduledType([{ type: "regenerate" }])).toBe("Regenerate");
    const row = scheduledRow(task());
    expect(row).toMatchObject({ type: "Price drop, Relist", on: true, nextRun: "Nov 5, 9:00 AM", lastRun: "—" });
    expect(row.when).toBe("Every day 9:00 AM · listed 60+ days on eBay");
    // A draft is confirmed from its form, not switched on from the list.
    expect(row.detail).toBe("drop 10% each cycle, floor $15, relist");
    expect(scheduledRow(task({ actions: [{ type: "regenerate" }] })).detail).toBe("");
    // The switch is always there; a draft shows it off and locked, with the word beside it.
    expect(scheduledRow(task({ status: "draft" }))).toMatchObject({ on: false, toggleable: false, state: "Draft", nextRun: "—" });
    expect(scheduledRow(task({ status: "paused" }))).toMatchObject({ on: false, toggleable: true, state: "", nextRun: "—" });
  });

  it("shows eBay offers as one row, switched by its saved rules", () => {
    expect(ebayOffersRow(ebay(), "UTC")).toMatchObject({ name: "eBay offers", on: false, when: "Every 15 minutes · No listings chosen", nextRun: "—" });
    expect(ebayOffersRow(ebay({ allListings: { enabled: true, discountPercent: 15, minimumPrice: "20" }, lastCheck: "2026-11-05T15:00:00Z" }), "UTC"))
      .toMatchObject({ on: true, when: "Every 15 minutes · All listings, 15% off, minimum $20", lastRun: "Nov 5, 3:00 PM", nextRun: "Nov 5, 3:15 PM" });
    const picked = ebayOffersRow(ebay({ listings: [
      { conversationId: "c1", listingId: "1", title: "Tee", ruleId: "r1", enabled: true, discountPercent: 10, minimumPrice: "5" },
      { conversationId: "c2", listingId: "2", title: "Hat", ruleId: "r2", enabled: false, discountPercent: 10, minimumPrice: "5" },
    ] }), "UTC");
    expect(picked).toMatchObject({ on: true, when: "Every 15 minutes · 1 listing", nextRun: "Within 15 min" });
    // Without a connection the switch stays off and locked, and the row says what it needs.
    expect(ebayOffersRow(ebay({ connected: false }), "UTC"))
      .toMatchObject({ on: false, toggleable: false, state: "Needs eBay", note: "Connect eBay to turn this on." });
  });

  it("shows sharing and offers to likers with why they are off", () => {
    expect(promotionRow(promotion())).toMatchObject({
      kind: "share", type: "Poshmark sharing", when: "Every day 9:00 AM, 8:30 PM · all live listings", on: false, note: "",
    });
    expect(promotionRow(promotion({ paused_reason: "Paused: Poshmark is limiting shares." })).note).toBe("Paused: Poshmark is limiting shares.");
    const mercari = promotionRow(promotion({ action: "offer", marketplace: "mercari", available: false, discount_percent: 10 }));
    expect(mercari).toMatchObject({ kind: "offer", type: "Offers to likers", when: "New likers on Mercari · 10% off all live listings" });
    expect(mercari.note).toBe("Mercari offers are not available in Anteroom yet");
    expect(promotionRow(promotion({ enabled: true, next_run_at: "2026-11-05T15:00:00Z" })))
      .toMatchObject({ on: true, nextRun: "Nov 5, 3:00 PM" });
  });
});
