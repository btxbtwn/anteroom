import { describe, expect, it } from "vitest";
import { hasMarketplaceLogo, marketplaceLogoBox, marketplaceLogoColor } from "./MarketplaceLogo";
import { OFFICIAL_LOGOS } from "./marketplaceLogos";

describe("MarketplaceLogo", () => {
  it("ships official Vendoo logos for core marketplaces", () => {
    for (const id of ["general", "ebay", "etsy", "poshmark", "mercari", "depop", "facebook"]) {
      expect(hasMarketplaceLogo(id)).toBe(true);
      expect(OFFICIAL_LOGOS[id].paths.length).toBeGreaterThan(0);
      expect(OFFICIAL_LOGOS[id].viewBox).toMatch(/^\d/);
    }
  });

  it("keeps brand fills on official path logos", () => {
    expect(marketplaceLogoColor("ebay")).toBe("#0064D2");
    expect(marketplaceLogoColor("poshmark")).toBe("#822533");
    expect(marketplaceLogoColor("depop")).toBe("#FF2300");
  });

  it("falls back for unknown marketplaces", () => {
    expect(hasMarketplaceLogo("unknown-market")).toBe(false);
    expect(marketplaceLogoColor("unknown-market")).toBe("#5C6578");
  });

  it("keeps square boxes by default", () => {
    expect(marketplaceLogoBox("ebay", 20)).toEqual({ width: 20, height: 20 });
  });

  it("draws wordmarks at their own proportions with about the same area, so none looks smaller", () => {
    expect(marketplaceLogoBox("ebay", 20, "wordmark")).toEqual({ width: 35, height: 14 });
    expect(marketplaceLogoBox("etsy", 20, "wordmark")).toEqual({ width: 31, height: 15 });
    expect(marketplaceLogoBox("depop", 20, "wordmark")).toEqual({ width: 42, height: 11 });
    // Square-ish marks stay square so they keep their size.
    expect(marketplaceLogoBox("poshmark", 20, "wordmark")).toEqual({ width: 20, height: 20 });
    expect(marketplaceLogoBox("unknown-market", 20, "wordmark")).toEqual({ width: 20, height: 20 });
  });
});
