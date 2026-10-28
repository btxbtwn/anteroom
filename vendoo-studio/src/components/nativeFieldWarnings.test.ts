import { describe, expect, it } from "vitest";
import { gapsByField } from "./nativeFieldWarnings";

const poshmarkRows = [
  { key: "poshmark_specifics.title" },
  { key: "poshmark_specifics.description" },
  { key: "poshmark_specifics.colors" },
  { key: "price" },
  { key: "direct_category_ids.poshmark" },
];

describe("direct List gaps on the form", () => {
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
