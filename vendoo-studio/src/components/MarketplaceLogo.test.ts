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

  it("draws eBay's shopping bag and Etsy/Depop as their one-letter marks at small sizes", () => {
    // Wide wordmarks in a 16px square were either a few pixels tall or wider
    // than the other rail marks.
    for (const id of ["ebay", "etsy", "depop"]) {
      const html = renderToStaticMarkup(createElement(MarketplaceLogo, { id, label: id, size: 16 }));
      expect(html).toContain(`viewBox="${OFFICIAL_LOGOS[id].compact?.viewBox}"`);
      expect(html).toContain(`aria-label="${id}"`);
      expect(html).toMatch(/width="14"/);
      expect(html).toMatch(/height="14"/);
    }
    expect(renderToStaticMarkup(createElement(MarketplaceLogo, { id: "etsy", label: "etsy", size: 16 })))
      .toContain(`fill="${marketplaceLogoColor("etsy")}"`);
    expect(renderToStaticMarkup(createElement(MarketplaceLogo, { id: "depop", label: "depop", size: 16 })))
      .toContain(`fill="${marketplaceLogoColor("depop")}"`);
    const ebay = renderToStaticMarkup(createElement(MarketplaceLogo, { id: "ebay", label: "eBay", size: 16 }));
    expect(ebay).toContain('fill="#E53238"');
    expect(ebay).toContain('fill="#0064D2"');
    expect(ebay).toContain('fill="#F5AF02"');
    expect(ebay).toContain('fill="#86B817"');
  });

  it("draws full wordmarks when asked for them", () => {
    const html = renderToStaticMarkup(createElement(MarketplaceLogo, { id: "depop", label: "Depop", size: 20, fit: "wordmark" }));
    expect(html).toContain(`viewBox="${OFFICIAL_LOGOS.depop.viewBox}"`);
    const ebay = renderToStaticMarkup(createElement(MarketplaceLogo, { id: "ebay", label: "eBay", size: 16, fit: "wordmark" }));
    expect(ebay).toContain(`viewBox="${OFFICIAL_LOGOS.ebay.viewBox}"`);
    const [, width, height] = /<svg width="([\d.]+)" height="([\d.]+)"/.exec(ebay) ?? [];
    expect({ width: Number(width), height: Number(height) }).toEqual(marketplaceLogoBox("ebay", 16, "wordmark"));
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
