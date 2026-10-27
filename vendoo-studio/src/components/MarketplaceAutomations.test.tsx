import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { MarketplaceAutomations } from "./MarketplaceAutomations";

const { useMutation, useQuery } = vi.hoisted(() => ({ useMutation: vi.fn(), useQuery: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({ useMutation, useQuery, useQueryClient: () => ({ invalidateQueries: vi.fn() }) }));

const base = { marketplace: "poshmark", scope: "all", conversation_ids: [], excluded_ids: [], min_days_listed: 0, share_order: "newest",
  speed: "normal", daily_cap: 300, times: ["09:00"], timezone: "UTC", discount_percent: 10, min_likes: 1, shipping_label: null,
  next_run_at: null, last_run: null };

beforeEach(() => {
  useMutation.mockReset();
  useMutation.mockReturnValue({ isPending: false, mutate: vi.fn(), reset: vi.fn() });
  useQuery.mockReset();
  useQuery.mockReturnValue({ isSuccess: true, isPending: false, data: {
    automations: [
      { ...base, id: "s", action: "share", name: "Closet sharing", enabled: false, available: true, paused_reason: "Paused: Poshmark is limiting shares." },
      { ...base, id: "m", marketplace: "mercari", action: "offer", name: "Mercari likers", enabled: false, available: false, paused_reason: null },
    ],
    supported: { poshmark: ["offer", "share"], mercari: ["offer"] }, awaiting_capture: { mercari: ["offer"] },
    speeds: ["slow", "normal", "fast"], orders: ["newest", "oldest", "random"], max_daily_cap: 6000, shipping_labels: [],
  } });
});

describe("marketplace automations", () => {
  it("shows every automation off with why, and turns nothing on by itself", () => {
    const enable = vi.spyOn(api.promotionAutomations, "enable");
    const html = renderToStaticMarkup(<MarketplaceAutomations />);
    expect(html).toContain("Sharing and offers to likers");
    expect(html).toContain("Paused: Poshmark is limiting shares.");
    expect(html).toContain("Mercari offers are not available in Anteroom yet");
    const turnOn = html.match(/<button[^>]*>Turn on<\/button>/g) || [];
    expect(turnOn.map((button) => button.includes('disabled=""'))).toEqual([false, true]);
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Pause all<\/button>/);
    expect(enable).not.toHaveBeenCalled();
    for (const [options] of useMutation.mock.calls) expect(options.retry).toBe(false);
  });
});
