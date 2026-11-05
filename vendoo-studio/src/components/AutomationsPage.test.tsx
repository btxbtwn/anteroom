import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Automation, AutomationApproval } from "../api/automations";
import type { PromotionAutomation } from "../api/promotions";
import { AutomationsPage } from "./AutomationsPage";
import { WorkspaceRuntimeProvider } from "../app/runtime";

const data: { automations: Automation[]; approvals: AutomationApproval[]; promotions: PromotionAutomation[]; ebayConnected: boolean } = {
  automations: [], approvals: [], promotions: [], ebayConnected: true,
};

vi.mock("@tanstack/react-query", () => ({
  useQuery: ({ queryKey }: { queryKey: string[] }) => ({
    data: queryKey[0] === "ebay-offers"
      ? { connected: data.ebayConnected, listings: [], history: [], lastCheck: null, lastError: null, checkIntervalMinutes: 15,
        allListings: { enabled: false, discountPercent: 10, minimumPrice: "" } }
      : queryKey[0] === "promotion-automations"
        ? { automations: data.promotions, supported: {}, awaiting_capture: {}, speeds: [], orders: [], max_daily_cap: 6000, shipping_labels: [] }
      : queryKey[0] === "automations" ? data.automations
        : queryKey[0] === "automation-approvals" ? data.approvals
          : [],
    isLoading: false,
    error: null,
  }),
  useMutation: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

const task = (overrides: Partial<Automation>): Automation => ({
  id: "a1",
  name: "Refresh stale listings",
  prompt: null,
  status: "active",
  timezone: "America/Chicago",
  schedule: { kind: "daily", time: "09:00" },
  filters: { min_age_days: 60 },
  actions: [{ type: "price", mode: "percent", value: 10, floor: 15, repeat: "step" }, { type: "relist" }],
  marketplaces: ["ebay", "poshmark"],
  summary: "",
  confirmed_at: "2026-11-01T00:00:00+00:00",
  next_run_at: "2026-11-05T15:00:00+00:00",
  last_run_at: "2026-11-04T15:00:00+00:00",
  created_at: null,
  updated_at: null,
  ...overrides,
});

const render = () => renderToStaticMarkup(<AutomationsPage onOpenListing={vi.fn()} />);

const hosted = (features: string[]) => renderToStaticMarkup(
  <WorkspaceRuntimeProvider value={{ hosted: true, features: new Set(features as never[]), views: new Set(["automations"]), marketplaceForms: new Set(), actionMarketplaces: new Set() }}>
    <AutomationsPage onOpenListing={vi.fn()} />
  </WorkspaceRuntimeProvider>,
);

const share = (overrides: Partial<PromotionAutomation>): PromotionAutomation => ({
  id: "s", marketplace: "poshmark", action: "share", name: "Closet sharing", scope: "all", conversation_ids: [], excluded_ids: [],
  min_days_listed: 0, share_order: "newest", speed: "normal", daily_cap: 300, times: ["09:00"], timezone: "UTC", discount_percent: 10,
  min_likes: 1, shipping_label: null, minimum_price: null, enabled: false, available: true, paused_reason: null, next_run_at: null,
  last_run: null, ...overrides,
});

describe("AutomationsPage", () => {
  beforeEach(() => {
    data.automations = [];
    data.approvals = [];
    data.promotions = [];
    data.ebayConnected = true;
  });

  it("opens on one list, with the forms behind New automation", () => {
    const html = render();
    expect(html).toContain("Automations");
    expect(html).toContain("nothing lists or delists until you approve each item");
    expect(html).toContain("New automation");
    // eBay offers is a row in the same list, not a block of settings.
    expect(html).toContain("eBay offers");
    expect(html).toContain("Every 15 minutes · No listings yet");
    expect(html).toContain('aria-label="Turn on eBay offers"');
    expect(html).not.toContain("Automatic offers check every 15 minutes");
    expect(html).not.toContain("Sharing and offers to likers");
    // The forms live in the side panel, closed until the seller asks for them.
    expect(html).not.toContain("Draft from prompt");
    expect(html).not.toContain("Save and confirm");
    expect(html).not.toContain("New sharing schedule");
    // Nothing is waiting, so the approval section stays out of the way.
    expect(html).not.toContain("Waiting for approval");
    expect(html).not.toContain("Require approval before publishing");
  });

  it("says when nothing is set up yet", () => {
    expect(hosted(["inventory"])).toContain("No automations yet.");
  });

  it("lists saved automations with their trigger, runs, a switch and a menu", () => {
    data.automations = [task({}), task({ id: "a2", name: "Weekend drop", status: "draft", next_run_at: null, last_run_at: null })];
    const html = render();
    expect(html).toContain("Refresh stale listings");
    expect(html).toContain("Every day 9:00 AM · listed 60+ days on eBay, Poshmark");
    expect(html).toContain("Nov 5, 9:00 AM");
    expect(html).toContain("Nov 4, 9:00 AM");
    expect(html).toContain('role="switch"');
    expect(html).toContain('aria-label="Pause Refresh stale listings"');
    expect(html).toContain('aria-label="More for Weekend drop"');
    // A draft is confirmed from its panel, not flipped on from the list.
    expect(html).toContain(">Draft<");
    expect(html).not.toContain("No automations yet.");
  });

  it("shows items waiting for approval, each approved on its own", () => {
    data.approvals = [{
      id: "p1", automation_id: "a1", run_id: "r1", conversation_id: "c1", title: "Blue tee",
      action: "relist", marketplaces: ["ebay"], state: "waiting", targets: {},
    } as unknown as AutomationApproval];
    const html = render();
    expect(html).toContain("Waiting for approval");
    expect(html).toContain("Blue tee");
    expect(html).toContain("Relist · eBay · waiting");
    expect(html).toContain("Review and approve");
  });

  it("on the web, shows eBay offers only while the offer timer runs", () => {
    const html = hosted(["inventory"]);
    expect(html).toContain("New automation");
    expect(html).not.toContain("eBay offers");
    expect(hosted(["inventory", "ebay_offers"])).toContain("eBay offers");
  });

  it("offers Connect eBay on the eBay row while eBay is not connected", () => {
    data.ebayConnected = false;
    const html = render();
    expect(html).toContain("Connect eBay");
    expect(html).not.toContain('aria-label="Turn on eBay offers"');
  });

  it("lists sharing and offers to likers in the same list, off with why", () => {
    data.promotions = [
      share({ paused_reason: "Paused: Poshmark is limiting shares." }),
      share({ id: "m", marketplace: "mercari", action: "offer", name: "Mercari likers", available: false }),
    ];
    const html = render();
    expect(html).toContain("Poshmark sharing");
    expect(html).toContain("Offers to likers");
    expect(html).toContain("Paused: Poshmark is limiting shares.");
    expect(html).toContain("Mercari offers are not available in Anteroom yet");
    // Each is off; one that isn't available yet can't be switched on.
    const sharing = html.match(/<[^>]*aria-label="Turn on Closet sharing"[^>]*>/)?.[0] ?? "";
    const likers = html.match(/<[^>]*aria-label="Turn on Mercari likers"[^>]*>/)?.[0] ?? "";
    expect(sharing).toContain('role="switch"');
    expect(sharing).not.toContain('data-disabled=""');
    expect(likers).toContain('data-disabled=""');
  });
});
