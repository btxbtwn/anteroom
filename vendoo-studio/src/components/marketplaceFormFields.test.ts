import { describe, expect, it } from "vitest";
import type { MarketplaceForm } from "../api/types";
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

  it("gives each listing value one row when the schema labels it differently", () => {
    const depopListing = {
      ...listing,
      depop_specifics: { productType: "jackets", parcelSize: "Medium", sizeType: "Regular" },
    };
    const depopSchema: MarketplaceForm[] = [{
      marketplace: "depop",
      category_id: "kids-jackets",
      known: true,
      fields: ["productType", "parcelSize", "sizeType"].map((key) => ({
        key,
        label: key.replace(/([A-Z])/g, " $1").replace(/^./, (c) => c.toUpperCase()),
        value: "",
        required: false,
        multi: false,
        selection_only: false,
        options: key === "parcelSize" ? ["Small", "Medium", "Large"] : [],
      })),
    }];
    const fields = fieldsForMarketplaceForm(depopListing, "depop", depopSchema);
    const keys = fields.map((field) => field.key.toLowerCase());
    // React keys rows by field key; a repeat leaves rows from the previous tab on screen.
    expect(new Set(keys).size).toBe(keys.length);
    const parcel = fields.filter((field) => field.key === "depop_specifics.parcelSize");
    expect(parcel).toHaveLength(1);
    expect(parcel[0].options).toEqual(["Small", "Medium", "Large"]);
  });

  it("names fields the way the marketplace does, not by their listing keys", () => {
    const depopListing = {
      ...listing,
      depop_specifics: {
        productType: "jackets",
        parcel_size: "Medium",
        category_path: "Kids,Coats and jackets,Jackets",
        category_id: "123",
      },
      etsy_specifics: { whoMade: "Another company or person" },
    };
    const depopSchema: MarketplaceForm[] = [{
      marketplace: "depop",
      category_id: "kids-jackets",
      known: true,
      fields: [{
        key: "productType", label: "Product type", value: "", required: true,
        multi: false, selection_only: false, options: [],
      }],
    }];
    const fields = fieldsForMarketplaceForm(depopListing, "depop", depopSchema);
    const labels = fields.map((field) => field.label);
    expect(labels).toContain("Product type *");
    expect(labels.filter((label) => label.toLowerCase() === "parcel size")).toEqual(["Parcel Size"]);
    expect(labels.some((label) => /[a-z][A-Z]|_/.test(label))).toBe(false);
    // Depop's own category replaces the shared one, and its id is not a form field.
    const category = fields.filter((field) => field.label === "Category");
    expect(category.map((field) => field.key)).toEqual(["depop_specifics.category_path"]);
    expect(labels).not.toContain("Category id");

    const etsy = fieldsForMarketplaceForm(depopListing, "etsy", undefined);
    expect(etsy.find((field) => field.key === "etsy_specifics.whoMade")?.label).toBe("Who Made It?");
  });

  it("leaves General as its own field list", () => {
    const labels = fieldsForMarketplaceForm(listing, "general", schema).map((field) => field.label);
    expect(labels.filter((label) => label === "Title")).toHaveLength(1);
    expect(labels).not.toContain("Sleeve Length *");
  });
});
