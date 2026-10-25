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

  it("leaves General as its own field list", () => {
    const labels = fieldsForMarketplaceForm(listing, "general", schema).map((field) => field.label);
    expect(labels.filter((label) => label === "Title")).toHaveLength(1);
    expect(labels).not.toContain("Sleeve Length *");
  });
});
