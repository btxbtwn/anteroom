import { describe, expect, it } from "vitest";
import {
  applyGoalHomeSnapshot,
  duplicatePair,
  patchHomeAfterDismiss,
  patchHomeAfterNotDuplicate,
  patchHomeAfterRestore,
  type AttentionItem,
  type HomeSummaryResponse,
} from "./home";

const baseItem = (key: string, kind: AttentionItem["kind"] = "failed_job"): AttentionItem => ({
  key,
  kind,
  priority: "high",
  title: key,
  detail: "detail",
  action_label: "Open",
  target: { view: "listings", conversation_id: "c1" },
  occurred_at: "2026-11-22T09:00:00Z",
});

const sample: HomeSummaryResponse = {
  today: {
    date: "2026-11-22",
    timezone: "UTC",
    units_listed: 0,
    drafts_created: 0,
    units_sold: 0,
    revenue: 0,
    profit: 0,
    profit_known: 0,
    shares_sent: 0,
    offers_sent: 0,
    automation_actions: 0,
  },
  goals: [],
  needs_attention: [baseItem("a"), baseItem("b", "still_listed")],
  dismissed: [],
  counts: { active: 2, dismissed: 0, by_kind: { failed_job: 1, still_listed: 1 } },
};

describe("patchHomeAfterDismiss / restore", () => {
  it("moves an active alert into dismissed with counts updated", () => {
    const next = patchHomeAfterDismiss(sample, sample.needs_attention[0]!, {
      item_key: "a",
      dismissed_at: "2026-11-22T12:00:00Z",
      expires_at: "2026-11-29T12:00:00Z",
      reappears_in_days: 7,
    });
    expect(next.needs_attention.map((item) => item.key)).toEqual(["b"]);
    expect(next.dismissed.map((item) => item.key)).toEqual(["a"]);
    expect(next.counts).toEqual({ active: 1, dismissed: 1, by_kind: { still_listed: 1 } });
    expect(next.dismissed[0]?.reappears_in_days).toBe(7);
  });

  it("restores a dismissed alert back to active", () => {
    const dismissed = patchHomeAfterDismiss(sample, sample.needs_attention[0]!);
    const restored = patchHomeAfterRestore(dismissed, "a");
    expect(restored.needs_attention.map((item) => item.key).sort()).toEqual(["a", "b"]);
    expect(restored.dismissed).toEqual([]);
    expect(restored.counts.active).toBe(2);
    expect(restored.needs_attention.find((item) => item.key === "a")?.reappears_in_days).toBeUndefined();
  });
});

describe("applyGoalHomeSnapshot", () => {
  it("keeps local attention when a goal snapshot is stale", () => {
    const local = patchHomeAfterDismiss(sample, sample.needs_attention[0]!);
    const server: HomeSummaryResponse = {
      ...sample,
      today: { ...sample.today, units_listed: 9 },
      goals: [{
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
      }],
    };
    const merged = applyGoalHomeSnapshot(server, local);
    expect(merged.today.units_listed).toBe(9);
    expect(merged.goals).toHaveLength(1);
    expect(merged.needs_attention.map((item) => item.key)).toEqual(["b"]);
    expect(merged.dismissed.map((item) => item.key)).toEqual(["a"]);
  });

  it("takes the server Active list when local already matches the server", () => {
    const merged = applyGoalHomeSnapshot(sample, sample);
    expect(merged.needs_attention.map((item) => item.key)).toEqual(["a", "b"]);
    expect(merged.dismissed).toEqual([]);
  });
});

describe("not a duplicate", () => {
  it("reads the two items from a duplicate alert's key", () => {
    expect(duplicatePair(baseItem("duplicate:c1:c2", "duplicate_listing"))).toEqual(["c1", "c2"]);
    expect(duplicatePair(baseItem("failed-job:c1:j1"))).toBeNull();
    expect(duplicatePair(baseItem("duplicate:c1", "duplicate_listing"))).toBeNull();
  });

  it("drops the alert from Active and Dismissed for good", () => {
    const home: HomeSummaryResponse = {
      ...sample,
      needs_attention: [...sample.needs_attention, baseItem("duplicate:c1:c2", "duplicate_listing")],
      counts: { active: 3, dismissed: 0, by_kind: { failed_job: 1, still_listed: 1, duplicate_listing: 1 } },
    };
    const next = patchHomeAfterNotDuplicate(home, "duplicate:c1:c2");
    expect(next.needs_attention.map((item) => item.key)).toEqual(["a", "b"]);
    expect(next.counts).toEqual({ active: 2, dismissed: 0, by_kind: { failed_job: 1, still_listed: 1 } });
    const dismissed = patchHomeAfterDismiss(home, home.needs_attention[2]!);
    expect(patchHomeAfterNotDuplicate(dismissed, "duplicate:c1:c2").dismissed).toEqual([]);
  });
});
