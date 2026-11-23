import { describe, expect, it } from "vitest";
import type { PromotionAutomation } from "../api/promotions";
import { bodyFromPromotionForm, describePromotionAutomation, emptyPromotionForm, formFromAutomation, withListing } from "./promotionAutomationsFormat";

const automation = (overrides: Partial<PromotionAutomation> = {}): PromotionAutomation => ({
  id: "a1", marketplace: "poshmark", action: "share", name: "Closet", scope: "all", conversation_ids: [], excluded_ids: ["x"],
  min_days_listed: 0, share_order: "newest", speed: "slow", daily_cap: 300, times: ["09:00", "20:00"], timezone: "America/Chicago",
  discount_percent: null, minimum_price: null, min_likes: null, shipping_label: null, enabled: false, available: true, paused_reason: null, next_run_at: null,
  last_run: null, ...overrides,
});

describe("promotion automation form", () => {
  it("builds a share schedule and refuses bad times or caps", () => {
    const form = { ...emptyPromotionForm("share", "poshmark", "America/Chicago"), times: "09:00, 20:00 09:00", dailyCap: "500" };
    expect(bodyFromPromotionForm(form, 6000)).toEqual({ body: { marketplace: "poshmark", action: "share", name: "", scope: "all",
      conversation_ids: [], excluded_ids: [], min_days_listed: 0, share_order: "newest", speed: "normal", daily_cap: 500,
      times: ["09:00", "20:00"], timezone: "America/Chicago" } });
    expect(bodyFromPromotionForm({ ...form, times: "9pm" }, 6000)).toHaveProperty("error");
    expect(bodyFromPromotionForm({ ...form, dailyCap: "6001" }, 6000)).toHaveProperty("error");
    expect(bodyFromPromotionForm({ ...form, scope: "items" }, 6000)).toHaveProperty("error");
  });

  it("sends a shipping discount only for Poshmark offers", () => {
    const offer = { ...emptyPromotionForm("offer"), discount: "20", minLikes: "2", shippingLabel: "-$1.50" };
    expect(bodyFromPromotionForm(offer, 6000)).toMatchObject({ body: { discount_percent: 20, min_likes: 2, shipping_label: "-$1.50" } });
    expect(bodyFromPromotionForm({ ...offer, marketplace: "mercari" }, 6000)).toMatchObject({ body: { shipping_label: null } });
    expect(bodyFromPromotionForm({ ...offer, discount: "0" }, 6000)).toHaveProperty("error");
  });

  it("includes and leaves out listings within the chosen scope", () => {
    const all = formFromAutomation(automation());
    expect(withListing(all, "x", true).excludedIds).toEqual([]);
    expect(withListing(all, "y", false).excludedIds).toEqual(["x", "y"]);
    const chosen = { ...all, scope: "items" as const, conversationIds: ["a"] };
    expect(withListing(chosen, "b", true).conversationIds).toEqual(["a", "b"]);
    expect(withListing(chosen, "a", false).conversationIds).toEqual([]);
  });

  it("describes what each automation does in the seller's words", () => {
    expect(describePromotionAutomation(automation())).toBe("Shares all live listings, 1 excluded at 09:00, 20:00 (America/Chicago) · newest listed first · slow · up to 300 a day");
    expect(describePromotionAutomation(automation({ action: "offer", discount_percent: 15, min_likes: 2, excluded_ids: [] })))
      .toContain("15% off to new likers on all live listings with 2+ likes · no shared minimum price; saved individual minimums also apply · no shipping discount");
  });

  it("saves an optional shared minimum and restores it when editing", () => {
    const offer = emptyPromotionForm("offer");
    expect(bodyFromPromotionForm(offer, 6000)).toMatchObject({ body: { minimum_price: null } });
    expect(bodyFromPromotionForm({ ...offer, minimumPrice: "25.50" }, 6000)).toMatchObject({ body: { minimum_price: "25.50" } });
    expect(formFromAutomation(automation({ minimum_price: "25.50" })).minimumPrice).toBe("25.50");
    for (const minimumPrice of ["0", "-1", "1.001", "NaN", "10000000"]) {
      expect(bodyFromPromotionForm({ ...offer, minimumPrice }, 6000)).toHaveProperty("error");
    }
    expect(describePromotionAutomation(automation({ action: "offer", minimum_price: "25.50" }))).toContain("minimum $25.50");
    expect(describePromotionAutomation(automation({ action: "offer" }))).toContain("checks recent likes about every minute");
  });

  it("supports Poshmark party/community modes, 24h intensity schedule, and 8,000 daily share cap", () => {
    const form = {
      ...emptyPromotionForm("share", "poshmark", "America/Los_Angeles"),
      shareMode: "party" as const,
      useHourlySchedule: true,
      dailyCap: "8000",
    };
    const res = bodyFromPromotionForm(form, 8000);
    expect("body" in res && res.body.daily_cap).toBe(8000);
    expect("body" in res && res.body.share_modes).toEqual(["party"]);
    expect("body" in res && res.body.hourly_schedule?.["10"]).toBe("medium");
    expect(bodyFromPromotionForm({ ...form, dailyCap: "8001" }, 8000)).toHaveProperty("error");
    expect(describePromotionAutomation(automation({ share_modes: ["party"], hourly_schedule: { "9": "high" } }))).toContain("Posh Party shares");
    expect(describePromotionAutomation(automation({ share_modes: ["party"], hourly_schedule: { "9": "high" } }))).toContain("24-hour intensity schedule");
  });

  it("validates waterfall offer sequences and Poshmark private bundle offers", () => {
    const wf = {
      ...emptyPromotionForm("offer", "poshmark"),
      waterfallSteps: [
        { discount: "10", delayMinutes: "15", shippingLabel: "" },
        { discount: "15", delayMinutes: "1440", shippingLabel: "" },
        { discount: "20", delayMinutes: "2880", shippingLabel: "" },
      ],
    };
    const ok = bodyFromPromotionForm(wf, 8000);
    expect("body" in ok && ok.body.waterfall_steps).toEqual([
      { discount_percent: 10, delay_minutes: 15 },
      { discount_percent: 15, delay_minutes: 1440 },
      { discount_percent: 20, delay_minutes: 2880 },
    ]);
    // Non-increasing discount fails validation
    const bad = bodyFromPromotionForm({
      ...wf,
      waterfallSteps: [
        { discount: "15", delayMinutes: "15", shippingLabel: "" },
        { discount: "15", delayMinutes: "1440", shippingLabel: "" },
      ],
    }, 8000);
    expect(bad).toHaveProperty("error");

    const bundle = {
      ...emptyPromotionForm("bundle_offer", "poshmark"),
      discount: "15",
      bundlePauseMinutes: "10",
      bundleMinItems: "2",
      bundleComment: "Thanks for liking!",
    };
    const bundleRes = bodyFromPromotionForm(bundle, 8000);
    expect(bundleRes).toMatchObject({
      body: {
        action: "bundle_offer",
        bundle_pause_minutes: 10,
        bundle_min_items: 2,
        bundle_comment: "Thanks for liking!",
      },
    });
    expect(describePromotionAutomation(automation({
      action: "bundle_offer",
      discount_percent: 15,
      bundle_pause_minutes: 10,
      bundle_min_items: 2,
    }))).toContain("Private bundle offers");
  });

  it("validates Poshmark automatic follows caps and Live Show opt-in", () => {
    const follow = {
      ...emptyPromotionForm("follow", "poshmark", "America/Chicago"),
      followReciprocal24h: true,
      followActiveSellersCap: "1000",
      unfollowCap: "500",
      unsubscribeLiveShows: false,
    };
    expect(bodyFromPromotionForm(follow, 8000)).toMatchObject({
      body: {
        action: "follow",
        follow_config: {
          reciprocal_24h: true,
          new_active_sellers_cap: 1000,
          unfollow_cap: 500,
          unsubscribe_live_shows: false,
        },
      },
    });
    expect(bodyFromPromotionForm({ ...follow, followActiveSellersCap: "1001" }, 8000)).toHaveProperty("error");
    expect(bodyFromPromotionForm({ ...follow, unfollowCap: "1001" }, 8000)).toHaveProperty("error");
    expect(describePromotionAutomation(automation({
      action: "follow",
      follow_config: { reciprocal_24h: true, new_active_sellers_cap: 500, unfollow_cap: 200, unsubscribe_live_shows: true },
    }))).toContain("reciprocal 24h");
  });
});
