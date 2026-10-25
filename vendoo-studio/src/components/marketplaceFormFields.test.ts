import { describe, expect, it } from "vitest";
import type { MarketplaceForm } from "../api/types";
import { formFieldText, getListingEditorValue } from "../listingPaths";
import { fieldsForMarketplaceForm } from "./ListingEditor";

const listing = {
  title: "Nike tee",
  description: "Cotton",
  price: 20,
  brand: "Nike",
  condition: "Pre-Owned - Good",
  mercari_specifics: { shippingLabel: "USPS Ground Advantage" },
};

const schema: MarketplaceForm[] = [{
  marketplace: "ebay",
  category_id: "53159",
  known: true,
  fields: [{
    key: "Sleeve Length",
    label: "Sleeve Length",
    value: "Short",
    required: true,
    multi: false,
    selection_only: true,
    options: ["Short", "Long"],
  }, {
    key: "Condition",
    label: "Condition",
    value: "Pre-owned",
    required: true,
    multi: false,
    selection_only: true,
    options: ["New", "Pre-owned"],
  }],
}];

describe("marketplace form fields", () => {
  it("shows the shared listing fields on a marketplace, then that marketplace's own", () => {
    const labels = fieldsForMarketplaceForm(listing, "mercari", undefined).map((field) => field.label);
    expect(labels.slice(0, 6)).toEqual(["Title", "Description", "Price", "Brand", "Condition", "Primary Color"]);
    expect(labels).toContain("Shipping Label");
    expect(labels).toContain("Package Weight (oz)");
    expect(labels.filter((label) => label === "Title")).toHaveLength(1);
  });

  it("keeps category specifics when a schema exists, without dropping title and price", () => {
    const fields = fieldsForMarketplaceForm(listing, "ebay", schema);
    const labels = fields.map((field) => field.label);
    expect(labels).toContain("Title");
    expect(labels).toContain("Price");
    expect(labels).toContain("Sleeve Length *");
    expect(labels.filter((label) => label.replace(/\s*\*$/, "") === "Condition")).toHaveLength(1);
    expect(fields.find((field) => field.label.startsWith("Condition"))?.options).toEqual(["New", "Pre-owned"]);
  });

  it("edits eBay's Best Offer prices as their own rows, never as [object Object]", () => {
    const offerListing = { ...listing, ebay_specifics: { bestOffer: { autoAcceptPrice: 15, minimumOfferPrice: 13 } } };
    const offerSchema: MarketplaceForm[] = [{ ...schema[0], fields: [{
      key: "Best Offer", label: "Best Offer", value: { autoAcceptPrice: 15 } as unknown as string,
      required: false, multi: false, selection_only: false, options: [],
    }] }];
    const fields = fieldsForMarketplaceForm(offerListing, "ebay", offerSchema);
    expect(fields.map((field) => field.key)).toEqual(expect.arrayContaining([
      "ebay_specifics.bestOffer.autoAcceptPrice", "ebay_specifics.bestOffer.minimumOfferPrice",
    ]));
    expect(fields.some((field) => field.key === "ebay_specifics.bestOffer" || field.label === "Best Offer")).toBe(false);
    expect(fields.find((field) => field.key.endsWith("autoAcceptPrice"))?.label).toBe("Auto Accept Price");
    expect(fields.find((field) => field.key.endsWith("minimumOfferPrice"))?.label).toBe("Minimum Offer Price");
  });

  it("does not put an object into a text field on any marketplace form", () => {
    const grouped = {
      ...listing,
      ebay_specifics: { bestOffer: { autoAcceptPrice: 15, minimumOfferPrice: 13 } },
      depop_specifics: { nationalShipping: { method: "Depop Shipping (USPS)", paidBy: "Buyer", parcelSize: "Under 1 lb" } },
      poshmark_specifics: { shippingDiscount: { type: "percent", amount: 10 } },
      mercari_specifics: { shipping: { deliveryMethod: "mercari_shipping", payerId: 1 } },
      etsy_specifics: { package: { length: 13, width: 10, height: 3 } },
      sellwild_specifics: { offer: { autoAccept: 12 } },
    };
    const objectSchema: MarketplaceForm[] = ["ebay", "depop", "poshmark", "mercari", "etsy", "sellwild"].map((marketplace) => ({
      marketplace,
      category_id: "1",
      known: true,
      fields: [{ key: "Grouped", label: "Grouped", value: { nested: true } as unknown as string, required: false, multi: false, selection_only: false, options: [] }],
    }));
    for (const tab of ["general", "ebay", "depop", "poshmark", "mercari", "etsy", "sellwild"]) {
      for (const field of fieldsForMarketplaceForm(grouped, tab, objectSchema)) {
        const shown = `${formFieldText(getListingEditorValue(grouped, field.key))} ${formFieldText(field.defaultValue)}`;
        expect(shown, `${tab} ${field.key}`).not.toContain("[object Object]");
      }
    }
  });

  it("leaves General as its own field list", () => {
    const labels = fieldsForMarketplaceForm(listing, "general", schema).map((field) => field.label);
    expect(labels.filter((label) => label === "Title")).toHaveLength(1);
    expect(labels).not.toContain("Sleeve Length *");
  });
});
