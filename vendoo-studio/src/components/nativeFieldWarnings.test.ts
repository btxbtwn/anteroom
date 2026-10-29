import { describe, expect, it } from "vitest";
import { gapsByField, rowForField } from "./nativeFieldWarnings";

const poshmarkRows = [
  { key: "poshmark_specifics.title" },
  { key: "poshmark_specifics.description" },
  { key: "poshmark_specifics.colors" },
  { key: "poshmark_specifics.inventoryRows" },
  { key: "price" },
  { key: "direct_category_ids.poshmark" },
];

const mercariRows = [
  { key: "mercari_specifics.title" },
  { key: "mercari_specifics.description" },
  { key: "mercari_specifics.brand" },
  { key: "mercari_specifics.condition" },
  { key: "mercari_specifics.nativeSizeId" },
  { key: "mercari_specifics.shippingClassId" },
  { key: "mercari_specifics.package.pounds" },
  { key: "price" },
  { key: "direct_category_ids.mercari" },
];

describe("direct List gaps on the form", () => {
  it.each(["ebay", "etsy", "poshmark", "mercari", "depop", "vinted", "sellwild"])(
    "%s displays field and photo messages without command metadata",
    (marketplace) => {
      const field = `${marketplace}_specifics.title`;
      const photos = `marketplace_photos.${marketplace}`;
      const gaps = [
        { field, message: "Enter a title.", blocking: true,
          fix: { field, command: "anteroom conversations fields --conv-id CONV --path title --value TITLE" } },
        { field: photos, message: "Choose photos.", blocking: false,
          fix: { field: photos, command: `anteroom conversations fields --conv-id CONV --path ${photos} --value []` } },
      ];
      expect([...gapsByField(marketplace, gaps, [{ key: field }, { key: photos }])]).toEqual([
        [field, ["Enter a title."]],
        [photos, ["Choose photos."]],
      ]);
    },
  );

  it("puts Poshmark's color answer under its Color row", () => {
    const found = gapsByField("poshmark", [{
      field: "primaryColor",
      message: "Poshmark does not list Navy, so that color is left off.",
      blocking: false,
    }], poshmarkRows);
    expect([...found]).toEqual([[
      "poshmark_specifics.colors",
      ["Poshmark does not list Navy, so that color is left off."],
    ]]);
  });

  it("finds the row a marketplace names its own way", () => {
    const found = gapsByField("poshmark", [
      { field: "title", message: "Poshmark titles are at most 80 characters.", blocking: true },
      { field: "price", message: "Poshmark lists this at $25.", blocking: false },
      { field: "direct_category_ids.poshmark", message: "Poshmark needs a category from its own list.", blocking: true },
    ], poshmarkRows);
    expect(found.get("poshmark_specifics.title")).toEqual(["Poshmark titles are at most 80 characters."]);
    expect(found.get("price")).toEqual(["Poshmark lists this at $25."]);
    expect(found.get("direct_category_ids.poshmark")).toEqual(["Poshmark needs a category from its own list."]);
  });

  it("finds the row behind Mercari's own field names", () => {
    expect(rowForField("mercari", "sizeId", mercariRows)).toBe("mercari_specifics.nativeSizeId");
    expect(rowForField("mercari", "name", mercariRows)).toBe("mercari_specifics.title");
    expect(rowForField("mercari", "categoryId", mercariRows)).toBe("direct_category_ids.mercari");
    expect(rowForField("mercari", "conditionId", mercariRows)).toBe("mercari_specifics.condition");
    expect(rowForField("mercari", "brandId", mercariRows)).toBe("mercari_specifics.brand");
    expect(rowForField("mercari", "weight", mercariRows)).toBe("mercari_specifics.package.pounds");
    // Mercari's photos answer is about its photo area, whether or not the form lists that row.
    expect(rowForField("mercari", "photos", mercariRows)).toBe("");
  });

  it("puts a marketplace's photos answer on its own photo area, not beside List", () => {
    const message = "You can add up to 8 photos. Depop shows the first 7, so leave one off or drag the ones you want first.";
    const rows = [{ key: "marketplace_photos.depop" }, { key: "depop_specifics.brand" }];
    const found = [...gapsByField("depop", [{ field: "photos", message, blocking: false }], rows)];
    expect(found).toEqual([["marketplace_photos.depop", [message]]]);
  });

  it("opens the row an Edit button is for, including the ones Poshmark names its own way", () => {
    expect(rowForField("poshmark", "primaryColor", poshmarkRows)).toBe("poshmark_specifics.colors");
    expect(rowForField("poshmark", "size", poshmarkRows)).toBe("poshmark_specifics.inventoryRows");
    expect(rowForField("poshmark", "price", poshmarkRows)).toBe("price");
    expect(rowForField("poshmark", "poshmark_specifics.sellerNotes", poshmarkRows)).toBe("");
  });

  it("keeps a listing-wide answer off the form, and repeats no message on one field", () => {
    const found = gapsByField("poshmark", [
      { field: "listing", message: "Something about the whole listing.", blocking: true },
      { field: "primaryColor", message: "Poshmark does not list Navy, so that color is left off.", blocking: false },
      { field: "secondaryColor", message: "Poshmark does not list Navy, so that color is left off.", blocking: false },
    ], poshmarkRows);
    expect(found.has("listing")).toBe(false);
    expect(found.get("poshmark_specifics.colors")).toHaveLength(1);
  });
});
