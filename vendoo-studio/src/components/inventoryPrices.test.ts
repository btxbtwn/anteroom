import { describe, expect, it } from "vitest";
import { marketplacePrice, marketplacePriceDescription } from "./inventoryPrices";

describe("marketplacePrice", () => {
  it("shows the live listing's price, marked when it is not the item price", () => {
    const listing = { price: 40, direct_records: {
      poshmark: { state: "published", price: 48 },
      ebay: { state: "pending_review", price: 40 },
    } };
    expect(marketplacePrice(listing, "poshmark")).toEqual({ amount: 48, label: "$48", differs: true });
    expect(marketplacePrice(listing, "ebay")).toEqual({ amount: 40, label: "$40", differs: false });
  });

  it("compares to the cent", () => {
    const listing = { price: 19.99, direct_records: { depop: { state: "published", price: 19.99 }, etsy: { state: "published", price: 19.5 } } };
    expect(marketplacePrice(listing, "depop")?.differs).toBe(false);
    expect(marketplacePrice(listing, "etsy")).toEqual({ amount: 19.5, label: "$19.50", differs: true });
  });

  it("shows a dash when a listed price is not known, never the item price", () => {
    expect(marketplacePrice({ price: 40, vendoo_marketplaces: ["mercari"] }, "mercari"))
      .toEqual({ amount: null, label: "—", differs: false });
    expect(marketplacePrice({ price: 40, direct_records: { mercari: { state: "published", price: null } } }, "mercari"))
      .toEqual({ amount: null, label: "—", differs: false });
  });

  it("has no price where the item is not listed", () => {
    expect(marketplacePrice({ price: 40 }, "ebay")).toBeNull();
    expect(marketplacePrice({ price: 40, direct_records: { ebay: { state: "draft", price: 40 } } }, "ebay")).toBeNull();
    expect(marketplacePrice({ price: 40, vendoo_marketplaces: ["ebay"], vendoo_sold_dates: { ebay: "2026-10-01" } }, "ebay")).toBeNull();
  });

  it("an imported listing keeps no stale direct price", () => {
    // The direct record was reset to a draft; the import still calls it listed.
    expect(marketplacePrice({ price: 40, vendoo_marketplaces: ["depop"], direct_records: { depop: { state: "draft", price: 55 } } }, "depop"))
      .toEqual({ amount: null, label: "—", differs: false });
  });
});

describe("marketplacePriceDescription", () => {
  it("names the item price beside a differing one", () => {
    expect(marketplacePriceDescription({ amount: 48, label: "$48", differs: true }, "Poshmark", 40)).toBe("$48 on Poshmark, item price $40");
    expect(marketplacePriceDescription({ amount: 40, label: "$40", differs: false }, "eBay", 40)).toBe("$40 on eBay");
    expect(marketplacePriceDescription({ amount: null, label: "—", differs: false }, "Mercari", 40)).toBe("Mercari price not known");
  });
});
