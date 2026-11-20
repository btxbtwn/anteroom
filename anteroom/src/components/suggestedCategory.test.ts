import { describe, expect, it } from "vitest";
import { suggestedCategory } from "./ListingEditor";

describe("suggestedCategory", () => {
  const listing = { direct_category_ids: { poshmark: "w/scarves", poshmark_suggested: true, depop: "kidswear_hats" } };

  it("marks only the category row of the tab whose closest option Anteroom picked", () => {
    expect(suggestedCategory(listing, "poshmark", "direct_category_ids.poshmark")).toBe(true);
    expect(suggestedCategory(listing, "poshmark", "poshmark_specifics.colors")).toBe(false);
    expect(suggestedCategory(listing, "depop", "direct_category_ids.depop")).toBe(false);
  });

  it("needs a category to check", () => {
    expect(suggestedCategory({ direct_category_ids: { poshmark_suggested: true } }, "poshmark", "direct_category_ids.poshmark")).toBe(false);
    expect(suggestedCategory(undefined, "poshmark", "direct_category_ids.poshmark")).toBe(false);
  });
});
