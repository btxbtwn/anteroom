import { describe, expect, it } from "vitest";
import {
  cloneListing,
  expandJoinedChoices,
  followedGeneralValue,
  marketplaceDisplayValue,
  formFieldText,
  formFieldValues,
  getListingEditorValue,
  getNestedValue,
  labelToJsonKey,
  ownedSpecificsValue,
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

  it("splits a comma-joined Depop style into the reference choices", () => {
    const choices = ["Casual", "Minimalist", "Indie", "Streetwear"];
    expect(expandJoinedChoices("Casual, Minimalist, Indie", choices)).toEqual(["Casual", "Minimalist", "Indie"]);
    expect(expandJoinedChoices(["Casual, Minimalist, Indie"], choices)).toEqual(["Casual", "Minimalist", "Indie"]);
    expect(expandJoinedChoices(["Casual"], choices)).toEqual(["Casual"]);
  });

});

describe("marketplace copies of general fields", () => {
  it("keeps saved nested offer edits instead of the cached native form values", () => {
    const listing = { price: 19.99, ebay_specifics: {} };
    const path = "ebay_specifics.bestOffer.minimumOfferPrice";
    const form = { tab: "ebay", native: true, defaultValue: "16.00" };
    expect(marketplaceDisplayValue(listing, path, form)).toBe("16.00");
    for (const value of [14, 16, ""]) {
      setListingEditorValue(listing, path, value);
      expect(marketplaceDisplayValue(cloneListing(listing), path, form)).toBe(value);
    }
    setListingEditorValue(listing, "ebay_specifics.bestOffer.autoAcceptPrice", 17);
    expect(marketplaceDisplayValue(listing, "ebay_specifics.bestOffer.autoAcceptPrice", {
      ...form, defaultValue: "18.00",
    })).toBe(17);
    setListingEditorValue(listing, "ebay_specifics.bestOffer.enabled", false);
    expect(marketplaceDisplayValue(listing, "ebay_specifics.bestOffer.enabled", {
      ...form, defaultValue: true,
    })).toBe(false);
  });

  it("reads a blank marketplace title as that form's own value", () => {
    const listing = { title: "General title", size: "L", ebay_specifics: { title: "" } };
    expect(ownedSpecificsValue(listing, "ebay_specifics.title")).toEqual({ owned: true, value: "" });
    expect(ownedSpecificsValue(listing, "depop_specifics.title")).toEqual({ owned: false, value: undefined });
    expect(followedGeneralValue(listing, "depop_specifics.title")).toBe("General title");
    expect(followedGeneralValue(listing, "ebay_specifics.Size")).toBe("L");
    expect(followedGeneralValue(listing, "ebay_specifics.conditionDescription")).toBeUndefined();
  });

  it("shows the live General title, and a marketplace's own size spelling", () => {
    const listing = { title: "New title", size: "M", ebay_specifics: { title: "" } };
    expect(marketplaceDisplayValue(listing, "ebay_specifics.title", {
      tab: "ebay", native: true, defaultValue: "Old title",
    })).toBe("");
    expect(marketplaceDisplayValue({ title: "New title", size: "M" }, "ebay_specifics.title", {
      tab: "ebay", native: true, defaultValue: "Old title",
    })).toBe("New title");
    expect(marketplaceDisplayValue(listing, "ebay_specifics.Size", {
      tab: "ebay", native: true, defaultValue: "Medium",
    })).toBe("Medium");
    expect(marketplaceDisplayValue({ title: "New title", quantity: 1 }, "ebay_specifics.quantity", {
      tab: "ebay", locked: true, defaultValue: 4,
    })).toBe(4);
    expect(marketplaceDisplayValue({ brand: "Nike", department: "Women" }, "ebay_specifics.brand", {
      tab: "ebay", native: true, defaultValue: "Old brand",
    })).toBe("Nike");
    expect(marketplaceDisplayValue({ brand: "Nike", department: "Women", ebay_specifics: { brand: "eBay brand" } }, "ebay_specifics.brand", {
      tab: "ebay", native: true, defaultValue: "Old brand",
    })).toBe("eBay brand");
    expect(marketplaceDisplayValue({ brand: "Nike", department: "Women" }, "ebay_specifics.department", {
      tab: "ebay", native: true, defaultValue: "Old department",
    })).toBe("Women");
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
  it("preserves native JSON key spelling when introducing size-group and size IDs", () => {
    const listing = { poshmark_specifics: {} };
    setListingEditorValue(listing, "poshmark_specifics.sizeSetId", "standard");
    setListingEditorValue(listing, "poshmark_specifics.nativeSizeId", "XL");
    expect(listing.poshmark_specifics).toEqual({ sizeSetId: "standard", nativeSizeId: "XL" });
    expect(labelToJsonKey("nativeSizeId")).toBe("nativeSizeId");
    expect(getListingEditorValue(listing, "poshmark_specifics.nativeSizeId")).toBe("XL");
  });
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
