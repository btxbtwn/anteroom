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
  it("keeps Mercari's native shipping label selector instead of hiding it as a legacy derived field", () => {
    const form: MarketplaceForm = { marketplace: "mercari", native: true, known: true, category_id: "1911", fields: [
      { key: "mercari_specifics.shippingClassId", path: "mercari_specifics.shippingClassId", label: "Shipping label", value: 2550,
        required: true, multi: false, selection_only: true, options: ["USPS Ground Advantage · $5.99"],
        option_values: { "USPS Ground Advantage · $5.99": "2550" } },
    ] };
    expect(fieldsForMarketplaceForm(listing, "mercari", [form]).find((field) => field.key === "mercari_specifics.shippingClassId"))
      .toMatchObject({ native: true, defaultValue: 2550, optionValues: { "USPS Ground Advantage · $5.99": "2550" } });
  });

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
    expect(fields.map((row) => row.key)).toEqual([
      "poshmark_specifics.nativeSizeId", "poshmark_specifics.styleTags", "poshmark_specifics.smartSell.enabled",
      "poshmark_specifics.poshmarkPrice",
    ]);
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
    const priceKey: Record<string, string> = {
      ebay: "ebay_specifics.price",
      poshmark: "poshmark_specifics.poshmarkPrice",
      mercari: "mercari_specifics.price",
      depop: "depop_specifics.price",
      sellwild: "sellwild_specifics.price",
    };
    const ebayOffer = [
      "ebay_specifics.price",
      "ebay_specifics.bestOffer.enabled",
      "ebay_specifics.bestOffer.autoAcceptPrice",
      "ebay_specifics.bestOffer.minimumOfferPrice",
    ];
    for (const tab of ["ebay", "poshmark", "mercari", "depop", "sellwild"]) {
      const expected = tab === "ebay" ? ebayOffer : [priceKey[tab]];
      expect(fieldsForMarketplaceForm(listing, tab, undefined).map((field) => field.key)).toEqual(expected);
      expect(fieldsForMarketplaceForm(listing, tab, [{ marketplace: tab, known: true, category_id: "1", fields: [] }]).map((field) => field.key)).toEqual(expected);
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

  it("shows an eBay price row and Depop measurement rows beside the native catalog", () => {
    const priced = fieldsForMarketplaceForm({
      ...listing,
      price: 29.99,
      ebay_specifics: { price: 23 },
    }, "ebay", undefined);
    expect(priced.find((field) => field.label === "eBay price")).toMatchObject({ key: "ebay_specifics.price" });
    const measured = fieldsForMarketplaceForm({
      ...listing,
      depop_specifics: {
        measurements: { unit: "in", values: [{ id: "pit_to_pit", label: "Pit to pit", value: "22", unit: "in" }] },
      },
    }, "depop", undefined);
    expect(measured.find((field) => field.label === "Pit to pit")).toMatchObject({
      key: "depop_specifics.measurements.values.0.value",
    });
    expect(measured.find((field) => field.label === "Depop price")?.key).toBe("depop_specifics.price");
  });

  it("shows Best Offer on every eBay form, as its own rows", () => {
    const fields = fieldsForMarketplaceForm(listing, "ebay", undefined);
    expect(fields.find((field) => field.key === "ebay_specifics.bestOffer.enabled")).toMatchObject({
      label: "Best Offer enabled", options: ["Yes", "No"], defaultValue: "Yes",
    });
    expect(fields.map((field) => field.key)).toEqual(expect.arrayContaining([
      "ebay_specifics.bestOffer.autoAcceptPrice", "ebay_specifics.bestOffer.minimumOfferPrice",
    ]));
    expect(fields.some((field) => field.label === "Best Offer" || field.key === "ebay_specifics.bestOffer")).toBe(false);
  });

  it("offers each marketplace's bottoms sizes, including waist, women's numbers and petite", () => {
    const jeans = {
      ...listing,
      title: "Levi's 32 Jeans",
      department: "Men",
      size: "32",
      category_path: "Clothing > Men > Jeans",
      ebay_specifics: { type: "Jeans" },
    };
    const ebay = fieldsForMarketplaceForm(jeans, "ebay", undefined);
    expect(ebay.find((field) => field.key === "ebay_specifics.size")?.options).toEqual(expect.arrayContaining(["32", "30"]));
    expect(ebay.find((field) => field.key === "ebay_specifics.inseam")?.options).toContain("30");
    expect(ebay.find((field) => field.key === "ebay_specifics.sizeType")?.options).toEqual(expect.arrayContaining(["Regular", "Big & Tall"]));
    const poshmark = fieldsForMarketplaceForm(jeans, "poshmark", undefined);
    expect(poshmark.find((field) => field.key === "poshmark_specifics.size")?.options).toContain("Waist 32");
    const pants = { ...listing, title: "Anne Klein 8 Pants", department: "Women", size: "8", category_path: "Clothing > Women > Pants" };
    for (const tab of ["ebay", "mercari", "depop", "etsy", "sellwild"]) {
      const options = fieldsForMarketplaceForm(pants, tab, undefined).find((field) => field.key === `${tab}_specifics.size`)?.options;
      expect(options).toEqual(expect.arrayContaining(["0", "8", "24"]));
    }
    expect(fieldsForMarketplaceForm(pants, "poshmark", undefined).find((field) => field.key === "poshmark_specifics.size")?.options)
      .toEqual(expect.arrayContaining(["8", "8P", "0", "24"]));
    expect(fieldsForMarketplaceForm(pants, "vinted", undefined).find((field) => field.key === "vinted_specifics.size")?.options)
      .toEqual(expect.arrayContaining(["S / 8", "XXL / 24", "XXS / 0"]));
    const depop = fieldsForMarketplaceForm(pants, "depop", undefined);
    expect(depop.find((field) => field.key === "depop_specifics.sizeGrouping")?.options).toEqual(expect.arrayContaining(["Petite", "Plus size", "Tall"]));
    const sellwild = fieldsForMarketplaceForm(jeans, "sellwild", undefined);
    expect(sellwild.find((field) => field.key === "sellwild_specifics.size")?.options).toContain("32x30");
  });

  it("shows each marketplace's category fields and that marketplace's choices", () => {
    const item = {
      ...listing,
      sku: "ABC",
      weight_oz: 8,
      ebay_specifics: { theme: "Western", size: "32" },
      poshmark_specifics: { holiday: "Christmas" },
      mercari_specifics: { holiday: "Christmas" },
      depop_specifics: { holiday: "Christmas" },
      etsy_specifics: { holiday: "Christmas" },
      sellwild_specifics: { holiday: "Christmas" },
      vinted_specifics: { holiday: "Christmas" },
    };
    for (const tab of ["ebay", "poshmark", "mercari", "depop", "etsy", "sellwild", "vinted"]) {
      const forms: MarketplaceForm[] = [{
        marketplace: tab,
        category_id: "11483",
        known: true,
        fields: [
          { key: "Material", label: "Material", value: "Cotton", required: true, multi: false, selection_only: false,
            options: ["Cotton", "Denim"], options_source: "marketplace", on_form: true },
          { key: "Holiday", label: "Holiday", value: "Christmas", required: false, multi: false, selection_only: true,
            options: ["Christmas"], options_source: "marketplace", on_form: false },
        ],
      }];
      const fields = fieldsForMarketplaceForm(item, tab, forms);
      const labels = fields.map((field) => field.label.replace(/\s*\*$/, ""));
      expect(labels, tab).toContain("Title");
      expect(labels, tab).toContain("Material");
      expect(labels, tab).not.toContain("Holiday");
      expect(labels, tab).not.toContain("SKU");
      if (tab === "ebay" || tab === "etsy" || tab === "poshmark" || tab === "mercari" || tab === "sellwild") {
        expect(labels, tab).not.toContain("Package Weight (oz)");
      }
      const material = fields.find((field) => field.label.startsWith("Material"));
      expect(material?.options, tab).toEqual(["Cotton", "Denim"]);
      expect(material?.selectionOnly, tab).toBe(true);
      const all = fieldsForMarketplaceForm(item, tab, forms, true).map((field) => field.label.replace(/\s*\*$/, ""));
      expect(all, tab).toContain("Holiday");
      expect(all, tab).toContain("SKU");
    }
  });
});
