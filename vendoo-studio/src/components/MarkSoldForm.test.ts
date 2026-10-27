import { describe, expect, it } from "vitest";
import { saleBody } from "./MarkSoldForm";
import { partialSale } from "./sidebar/listingStatus";

const fields = { marketplace: "other" as const, soldOn: "2026-10-01", quantity: "1", price: "$24.50", shippingCharged: "", fees: "0", shippingCost: "" };

describe("saleBody", () => {
  it("keeps blank amounts unknown and a zero as a real zero", () => {
    expect(saleBody(fields, "a".repeat(32))).toEqual({
      marketplace: "other", sold_on: "2026-10-01", quantity: 1, currency: "USD", request_id: "a".repeat(32),
      item_revenue: "24.50", shipping_credit: null, fees: "0", shipping_cost: null,
    });
  });

  it("refuses amounts, quantities and dates it cannot record", () => {
    expect(saleBody({ ...fields, price: "twenty" }, "a".repeat(32))).toMatch(/amounts/);
    expect(saleBody({ ...fields, quantity: "0" }, "a".repeat(32))).toMatch(/how many/);
    expect(saleBody({ ...fields, soldOn: "2999-01-01" }, "a".repeat(32))).toMatch(/day it sold/);
  });
});

describe("partialSale", () => {
  it("says how many units sold only while some are left", () => {
    expect(partialSale({ units_sold: 1, quantity: 3 }, "active")).toBe("1 of 3 sold");
    expect(partialSale({ units_sold: 3, quantity: 3 }, "sold")).toBeNull();
    expect(partialSale({}, "active")).toBeNull();
  });
});
