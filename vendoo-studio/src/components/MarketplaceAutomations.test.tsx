import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { PromotionAutomation } from "../api/promotions";
import { PromotionDetails, PromotionEditor } from "./MarketplaceAutomations";
import { emptyPromotionForm, formFromAutomation } from "./promotionAutomationsFormat";

const { useMutation, useQuery } = vi.hoisted(() => ({ useMutation: vi.fn(), useQuery: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({ useMutation, useQuery, useQueryClient: () => ({ invalidateQueries: vi.fn() }) }));

const rule = {
  id: "m", marketplace: "mercari", action: "offer", name: "Mercari likers", scope: "all", conversation_ids: [], excluded_ids: [],
  min_days_listed: 0, share_order: null, speed: null, daily_cap: null, times: [], timezone: "UTC", discount_percent: 10, min_likes: 1,
  shipping_label: null, minimum_price: null, enabled: false, available: false, paused_reason: null, next_run_at: null, last_run: null,
} as PromotionAutomation;

beforeEach(() => {
  useMutation.mockReset();
  useMutation.mockReturnValue({ isPending: false, mutate: vi.fn(), reset: vi.fn(), error: null });
  useQuery.mockReset();
  useQuery.mockImplementation(({ queryKey }: { queryKey: string[] }) => ({ isSuccess: true, isPending: false, data:
    queryKey.includes("coverage") ? { items: [], included: 0 }
      : queryKey.includes("activity") ? { runs: [], sends: [] }
        : { automations: [rule], supported: { poshmark: ["offer", "share"], mercari: ["offer"] }, awaiting_capture: { mercari: ["offer"] },
          speeds: ["slow", "normal", "fast"], orders: ["newest", "oldest", "random"], max_daily_cap: 6000, shipping_labels: [] },
  }));
});

describe("sharing and offer rule forms", () => {
  it("saves a new rule off, in one line, and turns nothing on by itself", () => {
    const enable = vi.spyOn(api.promotionAutomations, "enable");
    const html = renderToStaticMarkup(<PromotionEditor initial={emptyPromotionForm("offer")} onSaved={vi.fn()} onCancel={vi.fn()} />);
    expect(html).toContain("Saved off. Turn it on from the list; any edit turns it off again.");
    expect(html).toContain("At most one offer a day per listing");
    expect(html).toMatch(/<button[^>]*>Save<\/button>/);
    expect(enable).not.toHaveBeenCalled();
    for (const [options] of useMutation.mock.calls) expect(options.retry).toBe(false);
  });

  it("edits a sharing schedule with its round times and cap", () => {
    const html = renderToStaticMarkup(<PromotionEditor initial={{ ...emptyPromotionForm("share"), name: "Closet" }} onSaved={vi.fn()} onCancel={vi.fn()} />);
    expect(html).toContain("Round times (up to 6)");
    expect(html).toContain("6,000");
  });

  it("shows what a rule does, and saving its listings turns it off", () => {
    const html = renderToStaticMarkup(<PromotionDetails automation={rule} />);
    expect(html).toContain("no shared minimum price");
    expect(html).not.toContain("need a floor");
    expect(html).toContain("Save listings (turns it off)");
    expect(formFromAutomation(rule).id).toBe("m");
    for (const [options] of useMutation.mock.calls) expect(options.retry).toBe(false);
  });
});
