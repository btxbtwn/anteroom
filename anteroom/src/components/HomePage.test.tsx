// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { HomeSummaryResponse } from "../api/home";

const { api } = vi.hoisted(() => ({
  api: {
    home: {
      summary: vi.fn(),
      upsertGoal: vi.fn(),
      updateGoal: vi.fn(),
      deleteGoal: vi.fn(),
      dismissAttention: vi.fn(),
      restoreAttention: vi.fn(),
    },
  },
}));
vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: { ...actual.api, home: api.home } };
});

import { HomePage } from "./HomePage";

const sampleHome: HomeSummaryResponse = {
  today: {
    date: "2026-11-22",
    timezone: "UTC",
    units_listed: 4,
    drafts_created: 2,
    units_sold: 3,
    revenue: 245.5,
    profit: 142.25,
    profit_known: 3,
    shares_sent: 18,
    offers_sent: 5,
    automation_actions: 23,
  },
  goals: [
    {
      id: "goal_1",
      period: "monthly",
      metric: "profit",
      label: "Monthly Profit",
      custom_label: null,
      target: 2000,
      current: 1500,
      remaining: 500,
      progress_percent: 75,
      expected_percent: 70,
      pace: "ahead",
      window_start: "2026-11-01",
      window_end: "2026-11-30",
      days_elapsed: 22,
      days_total: 30,
      days_remaining: 8,
      unit: "currency",
      note: null,
    },
    {
      id: "goal_2",
      period: "weekly",
      metric: "listings",
      label: "Weekly Listings",
      custom_label: null,
      target: 20,
      current: 6,
      remaining: 14,
      progress_percent: 30,
      expected_percent: 60,
      pace: "behind",
      window_start: "2026-11-16",
      window_end: "2026-11-22",
      days_elapsed: 5,
      days_total: 7,
      days_remaining: 2,
      unit: "count",
      note: null,
    },
  ],
  needs_attention: [
    {
      key: "still_listed:conv_1",
      kind: "still_listed",
      priority: "high",
      title: "Vintage Wool Coat sold on eBay — still live on Poshmark",
      detail: "Delist it on Poshmark to prevent a double sale.",
      action_label: "Open listing",
      target: { view: "listings", conversation_id: "conv_1" },
      occurred_at: "2026-11-22T09:00:00Z",
    },
    {
      key: "expired_session:poshmark",
      kind: "expired_session",
      priority: "high",
      title: "Poshmark session expired",
      detail: "Reconnect your Poshmark session to resume sharing.",
      action_label: "Reconnect",
      target: { view: "settings", settings_section: "connections", target_id: "accounts" },
      occurred_at: "2026-11-22T08:00:00Z",
    },
  ],
  dismissed: [
    {
      key: "unsent_edits:conv_2",
      kind: "unsent_edits",
      priority: "medium",
      title: "Ceramic Pitcher has unsent edits",
      detail: "Review and send local edits to live marketplaces.",
      action_label: "Review edits",
      target: { view: "listings", conversation_id: "conv_2" },
      occurred_at: "2026-11-21T15:00:00Z",
      dismissed_at: "2026-11-22T07:00:00Z",
      expires_at: "2026-11-29T07:00:00Z",
      reappears_in_days: 7,
    },
  ],
  counts: {
    active: 2,
    dismissed: 1,
    by_kind: { still_listed: 1, expired_session: 1 },
  },
};

const container = document.createElement("div");
document.body.append(container);
let root = createRoot(container);

beforeEach(() => {
  vi.clearAllMocks();
  api.home.summary.mockResolvedValue(sampleHome);
  api.home.dismissAttention.mockResolvedValue({
    ok: true,
    dismissal: {
      item_key: "still_listed:conv_1",
      dismissed_at: "2026-11-22T12:00:00Z",
      expires_at: "2026-11-29T12:00:00Z",
      reappears_in_days: 7,
    },
  });
  api.home.restoreAttention.mockResolvedValue({
    ok: true,
    restored: true,
    item_key: "unsent_edits:conv_2",
  });
});

afterEach(async () => {
  await act(() => root.unmount());
  root = createRoot(container);
});

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

async function settle() {
  for (let i = 0; i < 4; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function renderHome(onNavigateAttention = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <HomePage onNavigateAttention={onNavigateAttention} />
      </QueryClientProvider>,
    );
  });
  await settle();
  return { onNavigateAttention };
}

describe("HomePage", () => {
  it("renders Today summary KPIs, goal progress bars, and Needs Attention items", async () => {
    const { onNavigateAttention } = await renderHome();
    const text = container.textContent ?? "";

    expect(text).toContain("Today");
    expect(text).toContain("Units listed");
    expect(text).toContain("Units sold");
    expect(text).toContain("$245.5");
    expect(text).toContain("$142.25");
    expect(text).toContain("23");

    expect(text).toContain("Monthly Profit");
    expect(text).toContain("$1,500");
    expect(text).toContain("$2,000");
    expect(text).toContain("Ahead");
    expect(text).toContain("Weekly Listings");
    expect(text).toContain("Behind pace");

    expect(text).toContain("Vintage Wool Coat sold on eBay — still live on Poshmark");
    expect(text).toContain("Poshmark session expired");

    const openBtn = [...container.querySelectorAll("button")].find((b) => b.textContent?.includes("Open listing"));
    expect(openBtn).toBeDefined();
    await act(() => openBtn!.click());
    expect(onNavigateAttention).toHaveBeenCalledWith({ view: "listings", conversation_id: "conv_1" });
  });

  it("supports dismissing active items and restoring from the Dismissed tab", async () => {
    await renderHome();

    const dismissBtn = [...container.querySelectorAll("button")].find((b) => b.textContent?.trim() === "Dismiss");
    expect(dismissBtn).toBeDefined();
    await act(async () => {
      dismissBtn!.click();
    });
    await settle();
    expect(api.home.dismissAttention).toHaveBeenCalledWith("still_listed:conv_1", "still_listed", expect.any(String));
    expect(container.textContent).not.toContain("Vintage Wool Coat sold on eBay — still live on Poshmark");

    const dismissedTab = [...container.querySelectorAll("button")].find((b) => b.textContent?.trim() === "Dismissed");
    expect(dismissedTab).toBeDefined();
    await act(async () => {
      dismissedTab!.click();
    });
    await settle();
    expect(container.textContent).toContain("Ceramic Pitcher has unsent edits");
    expect(container.textContent).toContain("Vintage Wool Coat sold on eBay — still live on Poshmark");

    const restoreBtn = [...container.querySelectorAll("button")].find(
      (b) => b.getAttribute("aria-label") === "Restore Ceramic Pitcher has unsent edits",
    );
    expect(restoreBtn).toBeDefined();
    await act(async () => {
      restoreBtn!.click();
    });
    await settle();
    expect(api.home.restoreAttention).toHaveBeenCalledWith("unsent_edits:conv_2", expect.any(String));
  });

  it("moves an alert out of Active as soon as Dismiss is clicked", async () => {
    let finishDismiss!: (result: { ok: boolean; dismissal: {
      item_key: string;
      dismissed_at: string;
      expires_at: string;
      reappears_in_days: number;
    } }) => void;
    api.home.dismissAttention.mockReturnValueOnce(new Promise((resolve) => { finishDismiss = resolve; }));
    await renderHome();

    expect(container.textContent).toContain("Vintage Wool Coat sold on eBay — still live on Poshmark");
    const dismissBtn = [...container.querySelectorAll("button")].find((b) => b.textContent?.trim() === "Dismiss");
    await act(() => dismissBtn!.click());
    await settle();

    expect(container.textContent).not.toContain("Vintage Wool Coat sold on eBay — still live on Poshmark");
    expect(api.home.dismissAttention).toHaveBeenCalledOnce();
    expect(api.home.summary).toHaveBeenCalledTimes(1);

    await act(() => finishDismiss({
      ok: true,
      dismissal: {
        item_key: "still_listed:conv_1",
        dismissed_at: "2026-11-22T12:00:00Z",
        expires_at: "2026-11-29T12:00:00Z",
        reappears_in_days: 7,
      },
    }));
    await settle();
    expect(container.textContent).not.toContain("Vintage Wool Coat sold on eBay — still live on Poshmark");
    expect(api.home.summary).toHaveBeenCalledTimes(1);
  });

  it("lets Restore of a different alert run while a slow Dismiss is still saving", async () => {
    let finishDismiss!: (result: { ok: boolean; dismissal: {
      item_key: string;
      dismissed_at: string;
      expires_at: string;
      reappears_in_days: number;
    } }) => void;
    api.home.dismissAttention.mockReturnValueOnce(new Promise((resolve) => { finishDismiss = resolve; }));
    await renderHome();

    const byLabel = (label: string) => [...container.querySelectorAll("button")]
      .find((node) => node.textContent?.trim() === label)!;
    await act(() => byLabel("Dismiss").click());
    await settle();
    expect(api.home.dismissAttention).toHaveBeenCalledOnce();

    await act(() => byLabel("Dismissed").click());
    const restorePitcher = [...container.querySelectorAll("button")].find(
      (node) => node.getAttribute("aria-label") === "Restore Ceramic Pitcher has unsent edits",
    )!;
    await act(() => restorePitcher.click());
    await settle();
    expect(api.home.restoreAttention).toHaveBeenCalledOnce();
    expect(container.textContent).not.toContain("Ceramic Pitcher has unsent edits");

    await act(() => finishDismiss({
      ok: true,
      dismissal: {
        item_key: "still_listed:conv_1",
        dismissed_at: "2026-11-22T12:00:00Z",
        expires_at: "2026-11-29T12:00:00Z",
        reappears_in_days: 7,
      },
    }));
    await settle();
    expect(api.home.summary).toHaveBeenCalledTimes(1);
  });

  it("keeps Restore when a slower same-alert Dismiss finishes later", async () => {
    let finishDismiss!: (result: { ok: boolean; dismissal: {
      item_key: string;
      dismissed_at: string;
      expires_at: string;
      reappears_in_days: number;
    } }) => void;
    let finishRestore!: (result: { ok: boolean; restored: boolean; item_key: string }) => void;
    api.home.dismissAttention.mockReturnValueOnce(new Promise((resolve) => { finishDismiss = resolve; }));
    api.home.restoreAttention.mockReturnValueOnce(new Promise((resolve) => { finishRestore = resolve; }));
    await renderHome();

    const byLabel = (label: string) => [...container.querySelectorAll("button")]
      .find((node) => node.textContent?.trim() === label)!;
    const coat = "Vintage Wool Coat sold on eBay — still live on Poshmark";

    await act(() => byLabel("Dismiss").click());
    await settle();
    expect(api.home.dismissAttention).toHaveBeenCalledOnce();
    expect(container.textContent).not.toContain(coat);

    await act(() => byLabel("Dismissed").click());
    const restoreCoat = [...container.querySelectorAll("button")].find(
      (node) => node.getAttribute("aria-label") === `Restore ${coat}`,
    )!;
    await act(() => restoreCoat.click());
    await settle();
    // Optimistic restore moves it out of Dismissed immediately; the API waits
    // for the in-flight dismiss of the same key.
    expect(container.textContent).not.toContain(coat);
    expect(api.home.restoreAttention).not.toHaveBeenCalled();

    await act(() => finishDismiss({
      ok: true,
      dismissal: {
        item_key: "still_listed:conv_1",
        dismissed_at: "2026-11-22T12:00:00Z",
        expires_at: "2026-11-29T12:00:00Z",
        reappears_in_days: 7,
      },
    }));
    await settle();
    // Stale dismiss success must not put the restored alert back under Dismissed.
    expect(container.textContent).not.toContain(coat);
    expect(api.home.restoreAttention).toHaveBeenCalledOnce();

    await act(() => byLabel("Active").click());
    await act(() => finishRestore({
      ok: true,
      restored: true,
      item_key: "still_listed:conv_1",
    }));
    await settle();
    expect(container.textContent).toContain(coat);
  });

  it("keeps a newer goal form when an earlier save completes", async () => {
    let finishSave!: (result: { ok: boolean; home: HomeSummaryResponse }) => void;
    api.home.updateGoal.mockReturnValueOnce(new Promise((resolve) => { finishSave = resolve; }));
    await renderHome();

    const button = (label: string) => [...container.querySelectorAll("button")]
      .find((node) => node.getAttribute("aria-label") === label || node.textContent?.trim() === label)!;
    const target = () => container.querySelector<HTMLInputElement>('[aria-label="Goal target"]');
    const setTarget = (value: string) => {
      const input = target()!;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    };

    await act(() => button("Edit Monthly Profit").click());
    await act(() => setTarget("2400"));
    await act(() => button("Save changes").click());
    await settle();
    expect(api.home.updateGoal).toHaveBeenCalledTimes(1);

    await act(() => button("Edit Weekly Listings").click());
    await act(() => setTarget("28"));
    expect(api.home.updateGoal).toHaveBeenNthCalledWith(
      1, "goal_1", { period: "monthly", metric: "profit", target: 2400, label: null }, expect.any(String),
    );
    await act(() => finishSave({ ok: true, home: sampleHome }));
    await settle();
    expect(target()?.value).toBe("28");

    api.home.updateGoal.mockResolvedValueOnce({ ok: true, home: sampleHome });
    await act(() => button("Save changes").click());
    await settle();
    expect(api.home.updateGoal).toHaveBeenNthCalledWith(
      2, "goal_2", { period: "weekly", metric: "listings", target: 28, label: null }, expect.any(String),
    );
    expect(target()).toBeNull();
  });

  it("keeps a Dismiss when a slower goal save returns a stale Active snapshot", async () => {
    let finishSave!: (result: { ok: boolean; home: HomeSummaryResponse }) => void;
    api.home.updateGoal.mockReturnValueOnce(new Promise((resolve) => { finishSave = resolve; }));
    await renderHome();

    const button = (label: string) => [...container.querySelectorAll("button")]
      .find((node) => node.getAttribute("aria-label") === label || node.textContent?.trim() === label)!;
    const coat = "Vintage Wool Coat sold on eBay — still live on Poshmark";

    await act(() => button("Edit Monthly Profit").click());
    await act(() => {
      const input = container.querySelector<HTMLInputElement>('[aria-label="Goal target"]')!;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "2400");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(() => button("Save changes").click());
    await settle();

    await act(() => button("Dismiss").click());
    await settle();
    expect(container.textContent).not.toContain(coat);
    expect(api.home.dismissAttention).toHaveBeenCalledOnce();

    await act(() => finishSave({ ok: true, home: sampleHome }));
    await settle();
    expect(container.textContent).not.toContain(coat);
  });

});
