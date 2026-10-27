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
  it("keeps native inventory rows and their category size groups", () => {
    const rows = [{ sizeSetId: "standard", sizeId: "M", quantity: 2, sku: "M-2" }];
    const groups = [{ id: "standard", label: "Standard", sizes: [{ id: "M", label: "Medium" }] }];
    const form: MarketplaceForm = { marketplace: "poshmark", native: true, known: true, category_id: "men/shirts", fields: [
      { key: "poshmark_specifics.inventoryRows", label: "Size and inventory", value: rows, required: true,
        multi: false, selection_only: false, options: [], input_type: "inventory", inventory_groups: groups },
    ] };
    expect(fieldsForMarketplaceForm(listing, "poshmark", [form])[0]).toMatchObject({
      type: "inventory", defaultValue: rows, inventoryGroups: groups,
    });
  });
  it("keeps the eBay variations object and the aspects eBay lets vary", () => {
    const value = { aspects: ["Size"], rows: [{ values: { Size: "M" }, quantity: 2 }, { values: { Size: "L" }, quantity: 1 }], photos: {} };
    const aspects = [{ name: "Size", selection_only: false, options: ["M", "L"] }];
    const form: MarketplaceForm = { marketplace: "ebay", native: true, known: true, category_id: "15687", fields: [
      { key: "ebay_specifics.variations", path: "ebay_specifics.variations", label: "Variations", value, required: false,
        multi: false, selection_only: false, options: [], input_type: "variations", variation_aspects: aspects },
    ] };
    expect(fieldsForMarketplaceForm(listing, "ebay", [form]).find((row) => row.key === "ebay_specifics.variations")).toMatchObject({
      type: "variations", defaultValue: value, variationAspects: aspects,
    });
  });
  it("keeps the category-specific flag so a category change can replace only those rows", () => {
    const form: MarketplaceForm = { marketplace: "depop", native: true, known: true, category_id: "womenswear_tops", fields: [
      { key: "price", path: "price", label: "Price", value: 20, required: true, multi: false, selection_only: false, options: [], input_type: "number" },
      { key: "fit", path: "depop_specifics.attributes.bottom-fit", label: "Fit", value: ["straight"], required: true,
        multi: true, selection_only: true, options: ["Straight"], category_specific: true },
    ] };
    const fields = fieldsForMarketplaceForm(listing, "depop", [form]);
    expect(fields.find((field) => field.key === "price")?.categorySpecific).toBeUndefined();
    expect(fields.find((field) => field.key === "depop_specifics.attributes.bottom-fit")?.categorySpecific).toBe(true);
  });

  it("preserves native numeric limits and precision", () => {
    const form: MarketplaceForm = { marketplace: "depop", native: true, known: true, category_id: "menswear_tshirts", fields: [
      { key: "depop_specifics.worldwideShipping.price", path: "depop_specifics.worldwideShipping.price", label: "Worldwide Shipping", value: 0,
        required: true, multi: false, selection_only: false, options: [], input_type: "number", min_value: 0, max_value: 99.99, step: 0.01 },
    ] };
    expect(fieldsForMarketplaceForm(listing, "depop", [form])[0]).toMatchObject({ minValue: 0, maxValue: 99.99, step: 0.01 });
  });
  it("keeps native free-text style tags editable outside the suggested tags", () => {
    const form: MarketplaceForm = { marketplace: "poshmark", native: true, known: true, category_id: "men/shirts", fields: [
      { key: "poshmark_specifics.styleTags", path: "poshmark_specifics.styleTags", label: "Style Tags", value: ["Boho"],
        required: false, multi: true, selection_only: false, options_source: "marketplace", options: ["Casual"] },
    ] };
    const fields = fieldsForMarketplaceForm(listing, "poshmark", [form]);
    expect(fields.find((row) => row.key === "poshmark_specifics.styleTags")?.selectionOnly).toBe(false);
  });
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

  it("keeps an eBay size when its size type matches either taxonomy parent", () => {
    const fields = [
      { key: "ebay_specifics.Size Type", label: "Size Type", defaultValue: "Regular" },
      { key: "ebay_specifics.Size", label: "Size", defaultValue: "M", options: ["M", "S"],
        valueConstraints: {
          M: [{ applicableForLocalizedAspectName: "Size Type", applicableForLocalizedAspectValues: ["Regular", "Petites"] }],
          S: [{ parentName: "Size Type", parentValues: ["Regular"] }],
        } },
    ];
    expect(nativeEditorDependencies(fields, { ebay_specifics: { sizeType: "Regular" } })[1].options).toEqual(["M", "S"]);
    expect(nativeEditorDependencies(fields, { ebay_specifics: { sizeType: "Petites" } })[1].options).toEqual(["M"]);
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

  it("fills bottoms sizes on the size fields that category includes", () => {
    const row = (tab: string, key: string, label: string) => ({
      key: `${tab}_specifics.${key}`, path: `${tab}_specifics.${key}`, label, value: "", required: false,
      multi: false, selection_only: false, options: [],
    });
    const formFor = (tab: string, keys: [string, string][]): MarketplaceForm => ({
      marketplace: tab, native: true, known: true, category_id: "jeans", fields: keys.map(([key, label]) => row(tab, key, label)),
    });
    const jeans = {
      ...listing,
      title: "Levi's 32 Jeans",
      department: "Men",
      size: "32",
      category_path: "Clothing > Men > Jeans",
      ebay_specifics: { type: "Jeans" },
    };
    const ebay = fieldsForMarketplaceForm(jeans, "ebay", [formFor("ebay", [["size", "Size"], ["inseam", "Inseam"], ["sizeType", "Size Type"]])]);
    expect(ebay.find((field) => field.key === "ebay_specifics.size")?.options).toEqual(expect.arrayContaining(["32", "30"]));
    expect(ebay.find((field) => field.key === "ebay_specifics.inseam")?.options).toContain("30");
    expect(ebay.find((field) => field.key === "ebay_specifics.sizeType")?.options).toEqual(expect.arrayContaining(["Regular", "Big & Tall"]));
    const poshmark = fieldsForMarketplaceForm(jeans, "poshmark", [formFor("poshmark", [["size", "Size"]])]);
    expect(poshmark.find((field) => field.key === "poshmark_specifics.size")?.options).toContain("Waist 32");
    const pants = { ...listing, title: "Anne Klein 8 Pants", department: "Women", size: "8", category_path: "Clothing > Women > Pants" };
    for (const tab of ["ebay", "mercari", "depop", "etsy", "sellwild"]) {
      const options = fieldsForMarketplaceForm(pants, tab, [formFor(tab, [["size", "Size"]])])
        .find((field) => field.key === `${tab}_specifics.size`)?.options;
      expect(options).toEqual(expect.arrayContaining(["0", "8", "24"]));
    }
    expect(fieldsForMarketplaceForm(pants, "poshmark", [formFor("poshmark", [["size", "Size"]])])
      .find((field) => field.key === "poshmark_specifics.size")?.options)
      .toEqual(expect.arrayContaining(["8", "8P", "0", "24"]));
    expect(fieldsForMarketplaceForm(pants, "vinted", [formFor("vinted", [["size", "Size"]])])
      .find((field) => field.key === "vinted_specifics.size")?.options)
      .toEqual(expect.arrayContaining(["S / 8", "XXL / 24", "XXS / 0"]));
    const depop = fieldsForMarketplaceForm(pants, "depop", [formFor("depop", [["sizeGrouping", "Size Grouping"]])]);
    expect(depop.find((field) => field.key === "depop_specifics.sizeGrouping")?.options).toEqual(expect.arrayContaining(["Petite", "Plus size", "Tall"]));
    const sellwild = fieldsForMarketplaceForm(jeans, "sellwild", [formFor("sellwild", [["size", "Size"]])]);
    expect(sellwild.find((field) => field.key === "sellwild_specifics.size")?.options).toContain("32x30");
    // A category that does not ask for inseam does not grow that row.
    expect(fieldsForMarketplaceForm(jeans, "ebay", [formFor("ebay", [["size", "Size"]])]).some((field) => field.key === "ebay_specifics.inseam")).toBe(false);
  });

  it("shows a marketplace's own controls until its category schema arrives", () => {
    const etsy = fieldsForMarketplaceForm({
      ...listing,
      etsy_specifics: { whoMade: "I did", clothingStyle: "Casual", sleeveLength: "Short" },
    }, "etsy", undefined);
    const labels = etsy.map((field) => field.label);
    expect(labels).toContain("Who Made It?");
    expect(labels).not.toContain("Clothing style");
    expect(labels).not.toContain("Sleeve length");
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
      if (["ebay", "poshmark", "mercari", "depop", "sellwild"].includes(tab)) {
        expect(all, tab).not.toContain("Holiday");
        expect(all, tab).not.toContain("SKU");
      } else {
        expect(all, tab).toContain("Holiday");
        expect(all, tab).toContain("SKU");
      }
    }
  });
});
