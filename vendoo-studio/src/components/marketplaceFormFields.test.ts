import { describe, expect, it } from "vitest";
import type { MarketplaceForm } from "../api/types";
import { fieldsForMarketplaceForm, nativeEditorDependencies } from "./ListingEditor";

const listing = {
  title: "Nike tee",
  description: "Cotton",
  price: 20,
  brand: "Nike",
  condition: "Pre-Owned - Good",
  mercari_specifics: { shippingLabel: "USPS Ground Advantage" },
};

describe("marketplace form fields", () => {
  it("uses only the native form's supported fields and keeps arrays, IDs and booleans", () => {
    const form: MarketplaceForm = { marketplace: "poshmark", native: true, known: true, category_id: "men/shirts", fields: [
      { key: "size", path: "poshmark_specifics.nativeSizeId", label: "Size", value: "123", required: true, multi: false,
        selection_only: true, options: ["XL"], option_values: { XL: "123" }, clears: ["poshmark_specifics.sizeSetId"] },
      { key: "style", path: "poshmark_specifics.styleTags", label: "Style Tags", value: ["Casual", "Retro"], required: false,
        multi: true, selection_only: false, options: ["Casual", "Retro"] },
      { key: "smart", path: "poshmark_specifics.smartSell.enabled", label: "Smart Pricing", value: false,
        input_type: "boolean", required: false, multi: false, selection_only: false, options: [] },
    ] };
    const fields = fieldsForMarketplaceForm(listing, "poshmark", [form]);
    expect(fields.map((row) => row.key)).toEqual(["poshmark_specifics.nativeSizeId", "poshmark_specifics.styleTags", "poshmark_specifics.smartSell.enabled"]);
    expect(fields[0]).toMatchObject({ optionValues: { XL: "123" }, defaultValue: "123", native: true });
    expect(fields[1].defaultValue).toEqual(["Casual", "Retro"]);
    expect(fields[2]).toMatchObject({ type: "boolean", defaultValue: false });
  });

  it("filters eBay dependent choices while keeping the saved value visible for correction", () => {
    const fields = [
      { key: "ebay_specifics.Brand", label: "Brand *", defaultValue: "Nike" },
      { key: "ebay_specifics.Model", label: "Model", defaultValue: "Air Max", options: ["Air Max", "Gazelle"],
        valueConstraints: { "Air Max": [{ parentName: "Brand", parentValues: ["Nike"] }], Gazelle: [{ parentName: "Brand", parentValues: ["Adidas"] }] } },
    ];
    expect(nativeEditorDependencies(fields, { ebay_specifics: { brand: "Nike" } })[1].options).toEqual(["Air Max"]);
    const updated = nativeEditorDependencies(fields, { ebay_specifics: { brand: "Adidas" } })[1];
    expect(updated.options).toEqual(["Gazelle"]);
    expect(updated.defaultValue).toBe("Air Max");
    expect(nativeEditorDependencies(fields, { ebay_specifics: { brand: "" } })[1].options).toEqual([]);
  });
  it("never substitutes a Vendoo or reference form for a direct marketplace", () => {
    for (const tab of ["ebay", "poshmark", "mercari", "depop", "sellwild"]) {
      expect(fieldsForMarketplaceForm(listing, tab, undefined)).toEqual([]);
      expect(fieldsForMarketplaceForm(listing, tab, [{ marketplace: tab, known: true, category_id: "1", fields: [] }])).toEqual([]);
    }
  });

  it("keeps General and the remaining marketplace editors available", () => {
    const general = fieldsForMarketplaceForm(listing, "general", undefined);
    expect(general.filter((field) => field.key === "title")).toHaveLength(1);
    expect(general.some((field) => field.key === "category_path")).toBe(true);
    const etsy = fieldsForMarketplaceForm({ ...listing, etsy_specifics: { whoMade: "Another company or person" } }, "etsy", undefined);
    expect(etsy.find((field) => field.key === "etsy_specifics.whoMade")?.label).toBe("Who Made It?");
    expect(etsy.filter((field) => field.key === "title")).toHaveLength(1);
  });
});
