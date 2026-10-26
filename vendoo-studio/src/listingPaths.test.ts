import { describe, expect, it } from "vitest";
import {
  cloneListing,
  formFieldText,
  formFieldValues,
  getListingEditorValue,
  getNestedValue,
  labelToJsonKey,
  resolveSpecificsKey,
  setListingEditorValue,
  setNestedValue,
} from "./listingPaths";

describe("formFieldText", () => {
  it("never renders a group of values as [object Object]", () => {
    expect(formFieldText({ autoAcceptPrice: 15, minimumOfferPrice: 13 })).toBe("");
    expect(formFieldText("[object Object]")).toBe("");
    expect(formFieldText(["Sportswear", { method: "buyer" }])).toBe("Sportswear");
    expect(formFieldText(15)).toBe("15");
    expect(formFieldText(["Kids", "Coats and jackets", "Jackets"])).toBe("Kids, Coats and jackets, Jackets");
  });
});

describe("multi-value form fields", () => {
  it("preserves separate selected values through an edit and save", () => {
    const listing = { ebay_specifics: { season: ["Spring", "Summer", "Fall"] } };
    const values = formFieldValues(getListingEditorValue(listing, "ebay_specifics.Season"));
    setListingEditorValue(listing, "ebay_specifics.Season", values.filter((value) => value !== "Summer"));
    expect(listing.ebay_specifics.season).toEqual(["Spring", "Fall"]);
    setListingEditorValue(listing, "ebay_specifics.Season", []);
    expect(getListingEditorValue(listing, "ebay_specifics.Season")).toEqual([]);
  });

  it("does not split a native option containing a comma", () => {
    expect(formFieldValues("Blue, Green", ["Blue, Green", "Red"])).toEqual(["Blue, Green"]);
    expect(formFieldValues(["Blue, Green", "Red"])).toEqual(["Blue, Green", "Red"]);
    expect(formFieldValues("Spring, Summer")).toEqual(["Spring", "Summer"]);
  });
});

describe("getNestedValue", () => {
  it("reads dotted paths through nested objects", () => {
    const listing = { ebay_specifics: { category_specifics: { neckline: "Crew Neck" } } };
    expect(getNestedValue(listing, "ebay_specifics.category_specifics.neckline")).toBe("Crew Neck");
  });

  it("returns undefined when a segment is missing or not an object", () => {
    expect(getNestedValue({ price: 45 }, "price.amount")).toBeUndefined();
    expect(getNestedValue({}, "ebay_specifics.size")).toBeUndefined();
    expect(getNestedValue(null, "title")).toBeUndefined();
  });
});

describe("setNestedValue", () => {
  it("creates intermediate objects and replaces non-object segments", () => {
    const listing: Record<string, unknown> = { depop_specifics: "corrupt" };
    setNestedValue(listing, "depop_specifics.parcelSize", "Small");
    setNestedValue(listing, "etsy_specifics.category_specifics.occasion", "Birthday");
    expect(listing).toEqual({
      depop_specifics: { parcelSize: "Small" },
      etsy_specifics: { category_specifics: { occasion: "Birthday" } },
    });
  });
});

describe("cloneListing", () => {
  it("returns a deep copy", () => {
    const original = { poshmark_specifics: { originalPrice: 80 } };
    const copy = cloneListing(original);
    setNestedValue(copy, "poshmark_specifics.originalPrice", 90);
    expect(original.poshmark_specifics.originalPrice).toBe(80);
  });

  it("treats a missing listing as empty", () => {
    expect(cloneListing(undefined)).toEqual({});
  });
});

describe("schema label ↔ listing JSON keys", () => {
  it("maps Vendoo labels onto camelCase listing keys", () => {
    expect(labelToJsonKey("Sleeve Length")).toBe("sleeveLength");
    expect(labelToJsonKey("Brand")).toBe("brand");
    expect(resolveSpecificsKey({ sleeveLength: "Short Sleeve" }, "Sleeve Length")).toBe("sleeveLength");
  });

  it("reads schema-labeled paths against camelCase specifics", () => {
    const listing = {
      ebay_specifics: {
        brand: "Bella+Canvas",
        sleeveLength: "Short Sleeve",
        accents: "Graphic",
      },
    };
    expect(getListingEditorValue(listing, "ebay_specifics.Brand")).toBe("Bella+Canvas");
    expect(getListingEditorValue(listing, "ebay_specifics.Sleeve Length")).toBe("Short Sleeve");
    expect(getListingEditorValue(listing, "ebay_specifics.Accents")).toBe("Graphic");
  });

  it("writes schema-labeled paths onto camelCase keys without leaving label duplicates", () => {
    const listing: Record<string, unknown> = {
      ebay_specifics: { sleeveLength: "Short Sleeve" },
    };
    setListingEditorValue(listing, "ebay_specifics.Sleeve Length", "Long Sleeve");
    setListingEditorValue(listing, "ebay_specifics.Accents", "Logo");
    expect(listing.ebay_specifics).toEqual({
      sleeveLength: "Long Sleeve",
      accents: "Logo",
    });
  });
});
