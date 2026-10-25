import { describe, expect, it } from "vitest";
import { groupFieldsIntoSections } from "./listingFieldSections";

const titles = (fields: { key: string; label: string; required?: boolean }[]) =>
  Object.fromEntries(groupFieldsIntoSections(fields).map((s) => [s.title, s.fields.map((f) => f.label)]));

describe("groupFieldsIntoSections", () => {
  it("groups the general tab by topic in display order", () => {
    const sections = groupFieldsIntoSections([
      { key: "title", label: "Title" },
      { key: "description", label: "Description" },
      { key: "price", label: "Price" },
      { key: "cost", label: "Cost" },
      { key: "quantity", label: "Quantity" },
      { key: "brand", label: "Brand" },
      { key: "condition", label: "Condition" },
      { key: "primaryColor", label: "Primary Color" },
      { key: "size", label: "Size" },
      { key: "sku", label: "SKU" },
      { key: "category_path", label: "Category" },
      { key: "weight_oz", label: "Package Weight (oz)" },
      { key: "package_dimensions_in", label: "Package Dimensions (LxWxH in)" },
    ]);
    expect(sections.map((s) => s.title)).toEqual([
      "Listing", "Pricing", "Condition", "Item", "Size & fit", "Color & material", "Shipping", "Inventory & identifiers",
    ]);
    expect(sections.find((s) => s.id === "item")?.fields.map((f) => f.label)).toEqual(["Category", "Brand"]);
  });

  it("sorts eBay schema attributes and puts required fields first", () => {
    const groups = titles([
      { key: "ebay_specifics.Theme", label: "Theme" },
      { key: "ebay_specifics.Size Type", label: "Size Type *", required: true },
      { key: "ebay_specifics.Fabric Weight", label: "Fabric Weight" },
      { key: "ebay_specifics.Unit Type", label: "Unit Type" },
      { key: "ebay_specifics.Sleeve Length", label: "Sleeve Length" },
      { key: "ebay_specifics.Size", label: "Size *", required: true },
      { key: "ebay_specifics.Garment Care", label: "Garment Care", required: false },
      { key: "ebay_specifics.Occasion", label: "Occasion *", required: true },
      { key: "depop_specifics.attribute_body-fit", label: "attribute_body-fit" },
    ]);
    expect(groups["Size & fit"]).toEqual(["Size *", "Size Type *", "attribute_body-fit", "Sleeve Length"]);
    expect(groups["Color & material"]).toEqual(["Fabric Weight"]);
    expect(groups["Inventory & identifiers"]).toEqual(["Unit Type"]);
    expect(groups["More details"]).toEqual(["Occasion *", "Theme", "Garment Care"]);
  });
});
