import { describe, expect, it } from "vitest";
import type { BoxTotals } from "../api/types";
import {
  boxProgress,
  boxScorecard,
  boxStoreKey,
  boxStoreName,
  formatPercent,
  profitPerHundred,
} from "./boxPurchases";

describe("bought boxes", () => {
  it("names known wholesale stores and keeps any other name", () => {
    expect(boxStoreName("tvf")).toBe("Thrift Vintage Fashion");
    expect(boxStoreName("Goodwill Bins")).toBe("Goodwill Bins");
    expect(boxStoreKey(" thrift vintage fashion ")).toBe("tvf");
    expect(boxStoreKey("Goodwill Bins")).toBe("Goodwill Bins");
  });

  it("describes progress and percentages", () => {
    expect(boxProgress({ sold: 4, listed: 9, listings: 12, sellable: 20 })).toBe("Sold 4 of 20 · 5 listed · 3 drafts");
    expect(boxProgress({ sold: 0, listed: 0, listings: 0, sellable: null })).toBe("Sold 0");
    expect(formatPercent(0.125)).toBe("13%");
    expect(formatPercent(null)).toBe("—");
  });

  it("grades a box the way a supplier is graded", () => {
    const totals = {
      sold: 8,
      asp: 31.4,
      sold_premium: 3,
      sell_through_by_age: [
        { days: 30, sold: 4, of: 20, rate: 0.2 },
        { days: 60, sold: 7, of: 20, rate: 0.35 },
      ],
      unsellable_rate: 0.1,
    } as BoxTotals;
    expect(boxScorecard(totals)).toEqual([
      "$31 average sale",
      "3 of 8 at $35+",
      "sold 20% by day 30, 35% by day 60",
      "10% unsellable",
    ]);
    expect(boxScorecard({ ...totals, asp: null, sell_through_by_age: [], unsellable_rate: null })).toEqual([]);
  });

  it("puts profit per $100 spent", () => {
    expect(profitPerHundred(0.423)).toBe("+$42 per $100 spent");
    expect(profitPerHundred(-0.5)).toBe("−$50 per $100 spent");
    expect(profitPerHundred(null)).toBeNull();
  });
});
