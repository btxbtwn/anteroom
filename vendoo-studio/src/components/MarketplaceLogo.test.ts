import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MarketplaceLogo, hasMarketplaceLogo, marketplaceLogoColor } from "./MarketplaceLogo";
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

  it("draws Etsy and Depop as their one-letter marks at small sizes, in brand color", () => {
    // Their wordmarks squeezed into a 16px square were 4-8px tall and unreadable.
    for (const id of ["etsy", "depop"]) {
      const html = renderToStaticMarkup(createElement(MarketplaceLogo, { id, label: id, size: 16 }));
      expect(html).toContain(`viewBox="${OFFICIAL_LOGOS[id].compact?.viewBox}"`);
      expect(html).toContain(`fill="${marketplaceLogoColor(id)}"`);
      expect(html).toContain(`aria-label="${id}"`);
    }
  });

  it("keeps eBay's wordmark at its own proportions instead of a square", () => {
    const html = renderToStaticMarkup(createElement(MarketplaceLogo, { id: "ebay", label: "eBay", size: 16 }));
    const [, width, height] = /<svg width="([\d.]+)" height="([\d.]+)"/.exec(html) ?? [];
    expect(Number(width) / Number(height)).toBeCloseTo(90 / 36, 1);
    // Tall enough to read: the wordmark's height is most of the requested size.
    expect(Number(height)).toBeGreaterThanOrEqual(12);
    expect(html).toContain('aria-label="eBay"');
  });
});
