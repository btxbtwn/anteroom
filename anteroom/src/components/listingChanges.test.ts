import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ListingChangeList } from "./ListingChangeList";
import { describe, expect, it } from "vitest";
import { blockerTarget, changeValue, listingChanges, matchingEditorField, fieldLabel } from "./listingChanges";

describe("listing review", () => {
  it("compares additions, removals, arrays and nested edits while hiding preparation metadata", () => {
    expect(listingChanges({ price: 30, brand: "Old", ebay_specifics: { size: "M" }, tags: ["old"], _vendoo_preparation: { a: 1 } },
      { price: 20, ebay_specifics: { size: "L" }, tags: ["new"], _vendoo_preparation: { a: 2 } })).toEqual([
      { field: "brand", before: "Old", after: undefined },
      { field: "ebay_specifics.size", before: "M", after: "L" },
      { field: "price", before: 30, after: 20 },
      { field: "tags", before: ["old"], after: ["new"] },
    ]);
  });
  it("keeps lock-only revisions restorable and describes them in seller terms", () => {
    const changes = listingChanges({ seller_fields: ["price"] },
      { seller_fields: ["price", "mercari_specifics.title"] });
    expect(changes).toHaveLength(1);
    expect(fieldLabel(changes[0].field)).toBe("Fields protected from automatic edits");
    expect(changeValue(changes[0].after, changes[0].field)).toBe("price, Mercari · title");
    expect(changeValue([], "seller_fields")).toBe("None");
  });
  it("renders protected field changes without internal paths or JSON", () => {
    const html = renderToStaticMarkup(createElement(ListingChangeList, { changes: listingChanges(
      { seller_fields: [] }, { seller_fields: ["price", "mercari_specifics.title"] },
    ) }));
    expect(html).toContain("Fields protected from automatic edits");
    expect(html).toContain("price, Mercari · title");
    expect(html).toContain("None");
    expect(html).not.toContain("seller_fields");
    expect(html).not.toContain("mercari_specifics");
  });
  it("does not hide zero and false", () => {
    expect(changeValue(0)).toBe("0");
    expect(changeValue(false)).toBe("false");
    expect(changeValue(null)).toBe("Empty");
  });
  it("routes each blocker to its marketplace or photos", () => {
    expect(blockerTarget("ebay_specifics.category_specifics.sleeveLength").tab).toBe("ebay");
    expect(blockerTarget("photos").reviewTab).toBe("input");
    expect(blockerTarget("price").tab).toBe("general");
  });
  it("opens a marketplace's own answer on that marketplace's form, not General", () => {
    expect(blockerTarget("primaryColor", "poshmark")).toEqual({ reviewTab: "forms", tab: "poshmark", field: "primaryColor" });
    expect(blockerTarget("price", "mercari").tab).toBe("mercari");
    // The field's own marketplace still wins when one is named.
    expect(blockerTarget("ebay_specifics.size", "poshmark").tab).toBe("poshmark");
  });
  it("sends a missing category to the marketplace form that has the category control", () => {
    expect(blockerTarget("direct_category_ids.depop").tab).toBe("depop");
    expect(blockerTarget("direct_category_ids.mercari").tab).toBe("mercari");
    expect(blockerTarget("marketplace_category_ids.ebay").tab).toBe("ebay");
    expect(blockerTarget("marketplace_category_ids.etsy").tab).toBe("etsy");
    expect(matchingEditorField([{ key: "direct_category_ids.etsy" }], "marketplace_category_ids.etsy")).toBe("direct_category_ids.etsy");
    expect(matchingEditorField([{ key: "price" }, { key: "direct_category_ids.ebay" }], "marketplace_category_ids.ebay")).toBe("direct_category_ids.ebay");
  });
  it("matches Vendoo labels to nested listing keys", () => {
    expect(matchingEditorField([{ key: "ebay_specifics.Sleeve Length" }], "ebay_specifics.category_specifics.sleeveLength")).toBe("ebay_specifics.Sleeve Length");
    expect(matchingEditorField([{ key: "price" }], "brand")).toBeUndefined();
  });
});
