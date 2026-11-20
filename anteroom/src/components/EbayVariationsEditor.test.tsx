import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { EbayVariations } from "../api/types";

const photos = [{ id: "p1", original_filename: "front.jpg", url: "/photos/p1.jpg" }, { id: "p2", original_filename: "back.jpg", url: "/photos/p2.jpg" }];
vi.mock("@tanstack/react-query", () => ({ useQuery: () => ({ data: photos }) }));

const { EbayVariationsEditor, normalizeVariations, variationTotal, withAspects } = await import("./EbayVariationsEditor");

const variations: EbayVariations = {
  aspects: ["Size", "Color"], photosBy: "Color",
  rows: [{ values: { Size: "M", Color: "Red" }, quantity: 2, sku: "TEE-M-RED", price: "" },
    { values: { Size: "L", Color: "Blue" }, quantity: "3", sku: "", price: "24.50" }],
  photos: { Red: ["p1"] },
};

describe("eBay variations", () => {
  it("renders each variation's values, stock, price and SKU, and per-color photos, without saving", () => {
    const html = renderToStaticMarkup(<EbayVariationsEditor convId="c1" listing={{}} value={variations}
      aspects={[{ name: "Size", selection_only: false, options: ["S", "M", "L"] }, { name: "Color", selection_only: true, options: ["Red", "Blue"] }]}
      onChange={() => { throw new Error("Rendering must not save"); }} />);
    expect(html).toContain('aria-label="eBay variations"');
    expect(html).toContain('value="TEE-M-RED"');
    expect(html).toContain('value="24.50"');
    expect(html).toContain('aria-label="Variation quantity 2"');
    expect(html).toContain("2 variations · 5 in stock");
    expect(html).toContain('aria-label="Photos for Red"');
    expect(html).toContain('aria-label="Remove front.jpg for Red"');
    expect(html).toContain('aria-label="Use back.jpg for Blue"');
    expect(html).toContain("Add variation");
  });

  it("offers only the photos the eBay form sends", () => {
    const html = renderToStaticMarkup(<EbayVariationsEditor convId="c1" listing={{ marketplace_photos: { ebay: ["p2"] } }} value={variations}
      aspects={[{ name: "Size", selection_only: false, options: ["M", "L"] }, { name: "Color", selection_only: true, options: ["Red", "Blue"] }]}
      onChange={() => undefined} />);
    expect(html).toContain('aria-label="Use back.jpg for Blue"');
    expect(html).not.toContain("front.jpg");
  });

  it("totals stock, ignoring blank and invalid quantities", () => {
    expect(variationTotal(variations)).toBe(5);
    expect(variationTotal({ ...variations, rows: [...variations.rows, { values: {}, quantity: "" }, { values: {}, quantity: -1 }] })).toBe(5);
  });

  it("keeps row values for aspects that stay and drops photos when the photo aspect goes", () => {
    const sizeOnly = withAspects(variations, ["Size"]);
    expect(sizeOnly.rows.map(row => row.values)).toEqual([{ Size: "M" }, { Size: "L" }]);
    expect(sizeOnly.photos).toEqual({});
    expect(sizeOnly.photosBy).toBeUndefined();
    expect(withAspects(variations, ["Color", "Size"]).photos).toEqual({ Red: ["p1"] });
  });

  it("reads a missing or partial value as no variations", () => {
    expect(normalizeVariations(undefined)).toEqual({ aspects: [], rows: [], photos: {} });
    expect(normalizeVariations({ aspects: ["Size"], rows: "bad" })).toEqual({ aspects: ["Size"], rows: [], photos: {} });
  });
});
