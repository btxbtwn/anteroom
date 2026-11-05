import { describe, expect, it } from "vitest";
import type { SalesOverviewFigures } from "../api/salesLedger";
import { axisTicks, overviewFigures, weekLabel } from "./analyticsFormat";

const figures = (overrides: Partial<SalesOverviewFigures> = {}): SalesOverviewFigures => ({
  count: 4, revenue: 200, revenue_known: 4, profit: 120, profit_known: 4, fees_known: 4,
  average_days: 12, days_known: 4, sell_through: 25, ...overrides,
});

const byLabel = (cards: ReturnType<typeof overviewFigures>) => Object.fromEntries(cards.map((card) => [card.label, card]));

describe("overviewFigures", () => {
  it("shows each figure with its change against the previous period", () => {
    const cards = byLabel(overviewFigures(figures(), figures({ count: 2, revenue: 100, revenue_known: 2, profit: 60, profit_known: 2, fees_known: 2, average_days: 20, days_known: 2 }), false));
    expect(cards.Revenue.value).toBe("$200");
    expect(cards.Revenue.change).toBe("Up $100 (100%)");
    expect(cards.Profit.change).toBe("Up $60 (100%)");
    expect(cards["Items sold"].change).toBe("Up 2 (100%)");
    expect(cards["Average days to sell"].value).toBe("12 days");
    expect(cards["Average days to sell"].change).toBe("Down 8 days (40%)");
    expect(cards["Sell-through"].value).toBe("25%");
    expect(cards["Sell-through"].change).toBeUndefined();
  });

  it("says when there is not enough data instead of showing zero", () => {
    const cards = byLabel(overviewFigures(figures({ revenue: 0, revenue_known: 0, profit: null, profit_known: 0, average_days: null, days_known: 0, sell_through: null }), null, true));
    expect(cards.Revenue.value).toBeNull();
    expect(cards.Profit.value).toBeNull();
    expect(cards["Average days to sell"].value).toBeNull();
    expect(cards["Sell-through"].value).toBeNull();
    expect(cards["Sell-through"].hint).toContain("all marketplaces");
    expect(cards["Items sold"].value).toBe("4");
  });

  it("shows zero revenue and profit when nothing sold", () => {
    const none = figures({ count: 0, revenue: 0, revenue_known: 0, profit: null, profit_known: 0, fees_known: 0, average_days: null, days_known: 0 });
    const cards = byLabel(overviewFigures(none, none, false));
    expect(cards.Revenue.value).toBe("$0");
    expect(cards.Profit.value).toBe("$0");
    expect(cards["Items sold"].change).toBe("No change");
  });

  it("does not compare totals that are missing amounts", () => {
    const cards = byLabel(overviewFigures(figures({ revenue_known: 3, fees_known: 2 }), figures(), false));
    expect(cards.Revenue.change).toBe("No comparison: missing prices");
    expect(cards.Profit.change).toBe("No comparison: missing costs or fees");
    expect(cards.Revenue.hint).toBe("Recorded on 3 of 4 sales");
  });
});

describe("axisTicks", () => {
  it("rounds to clean steps that cover the peak", () => {
    expect(axisTicks(7)).toEqual([0, 2, 4, 6, 8]);
    expect(axisTicks(430)).toEqual([0, 100, 200, 300, 400, 500]);
    expect(axisTicks(0)).toEqual([0, 1]);
    expect(axisTicks(2, true)).toEqual([0, 1, 2]);
    expect(axisTicks(2)).toEqual([0, 0.5, 1, 1.5, 2]);
  });
});

describe("weekLabel", () => {
  it("names the Monday a week starts on", () => {
    expect(weekLabel("2026-10-26")).toBe("Oct 26");
  });
});
