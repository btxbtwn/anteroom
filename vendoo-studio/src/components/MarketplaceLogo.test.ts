import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MarketplaceLogo, hasMarketplaceLogo, marketplaceLogoBox, marketplaceLogoColor } from "./MarketplaceLogo";
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
    // Sized like the sidebar's wordmarks, not squeezed to 5px tall.
    expect({ width: Number(width), height: Number(height) }).toEqual(marketplaceLogoBox("ebay", 16, "wordmark"));
    expect(html).toContain('aria-label="eBay"');
  });

  it("draws full wordmarks when asked for them", () => {
    const html = renderToStaticMarkup(createElement(MarketplaceLogo, { id: "depop", label: "Depop", size: 20, fit: "wordmark" }));
    expect(html).toContain(`viewBox="${OFFICIAL_LOGOS.depop.viewBox}"`);
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
