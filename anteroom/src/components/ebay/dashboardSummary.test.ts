import { describe, expect, it } from "vitest";
import type { EbayRow } from "../../api/ebay";
import { displayMoney, summarizeDisplayedEarnings } from "./dashboardSummary";

const order = (amount: string, cost: string | null, profit: string | null): EbayRow => ({ id: "order", title: "Order", subtitle: "", status: "", amount,
  fields: { "Item cost": cost, Profit: profit }, children: [] });

describe("visible eBay order summaries", () => {
  it("adds only supplied rows and preserves cent amounts", () => {
    expect(summarizeDisplayedEarnings([order("0.10", "0.02", "0.08"), order("0.20", "0.03", "0.17")])).toEqual({ earnings: .3, costs: .05, profit: .25 });
  });
  it("does not turn missing costs into profit or missing orders into zero sales", () => {
    expect(summarizeDisplayedEarnings([order("50", null, null)])).toEqual({ earnings: 50, costs: null, profit: null });
    expect(summarizeDisplayedEarnings([])).toEqual({ earnings: null, costs: null, profit: null });
  });
  it("shows an actual zero and a loss without inventing a positive result", () => {
    expect(summarizeDisplayedEarnings([order("0", "5", "-5")])).toEqual({ earnings: 0, costs: 5, profit: -5 });
    expect(displayMoney(0)).toBe("$0.00");
    expect(displayMoney(null)).toBe("Not available");
  });
});
