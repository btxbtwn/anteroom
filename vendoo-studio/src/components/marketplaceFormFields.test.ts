import { describe, expect, it } from "vitest";
import type { MarketplaceForm } from "../api/types";
import { fieldsForMarketplaceForm, nativeEditorDependencies, nativeFormSections } from "./ListingEditor";

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
  it("shows Depop's own Size and Size grouping once on a bottoms listing", () => {
    const jeans = { ...listing, title: "Jack & Jones jeans", category_path: "Men > Jeans", department: "Men",
      depop_specifics: { size: "34\"", sizeGrouping: "Does Not Apply" } };
    const form: MarketplaceForm = { marketplace: "depop", native: true, known: true, category_id: "menswear_jeans", fields: [
      { key: "depop_specifics.nativeSizeId", path: "depop_specifics.nativeSizeId", label: "Size", value: "34", required: true,
        multi: false, selection_only: true, options: ["34"], options_source: "marketplace" },
      { key: "depop_specifics.attributes.body-fit", path: "depop_specifics.attributes.body-fit", label: "Size grouping", value: [],
        required: false, multi: true, selection_only: true, options: ["Petite", "Tall"], options_source: "marketplace" },
    ] };
    const labels = fieldsForMarketplaceForm(jeans, "depop", [form]).map((field) => field.label.replace(/\s*\*$/, "").toLowerCase());
    expect(labels.filter((label) => label === "size")).toHaveLength(1);
    expect(labels.filter((label) => label === "size grouping")).toHaveLength(1);
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
  it("keeps a marketplace form's headings, order and half-width rows", () => {
    const row = (key: string, label: string, section: string, width?: "half") => ({ key, path: key, label, value: "",
      required: false, multi: false, selection_only: false, options: [], section, ...(width ? { width } : {}) });
    const form: MarketplaceForm = { marketplace: "depop", native: true, known: true, category_id: "menswear_jeans", fields: [
      row("depop_specifics.description", "Description", "Description"),
      row("depop_specifics.brand", "Brand", "Info"),
      row("depop_specifics.nativeSizeId", "Size", "Info", "half"),
      row("quantity", "Quantity", "Info", "half"),
      row("depop_specifics.source", "Source", "Enhance your listing"),
      row("price", "Item price", "Item price"),
    ] };
    const fields = fieldsForMarketplaceForm(listing, "depop", [form]);
    expect(nativeFormSections(fields).map((section) => [section.title, section.fields.map((field) => field.label)])).toEqual([
      ["Description", ["Description"]], ["Info", ["Brand", "Size", "Quantity"]],
      ["Enhance your listing", ["Source"]], ["Item price", ["Item price"]],
    ]);
    expect(fields.filter((field) => field.width === "half").map((field) => field.label)).toEqual(["Size", "Quantity"]);
  });

  it("keeps a marketplace's photo area where its page has it, with no heading where the page has none", () => {
    const form: MarketplaceForm = { marketplace: "mercari", native: true, known: true, category_id: "1", fields: [
      { key: "marketplace_photos.mercari", path: "marketplace_photos.mercari", label: "", value: [], required: false, multi: false,
        selection_only: false, options: [], input_type: "photos", help: "Add up to 12 photos", section: "",
        slot_labels: ["Cover photo"], accept: "image/jpeg,image/png,.jpg,.jpeg,.png" },
      { key: "mercari_specifics.title", path: "mercari_specifics.title", label: "Title", value: "", required: true, multi: false,
        selection_only: false, options: [], section: "Product info" },
    ] };
    const fields = fieldsForMarketplaceForm(listing, "mercari", [form]);
    expect(fields[0]).toMatchObject({ key: "marketplace_photos.mercari", type: "photos", section: "", help: "Add up to 12 photos",
      slotLabels: ["Cover photo"], accept: "image/jpeg,image/png,.jpg,.jpeg,.png" });
    expect(nativeFormSections(fields).map((section) => [section.title, section.fields.map((field) => field.key)])).toEqual([
      ["", ["marketplace_photos.mercari"]], ["Product info", ["mercari_specifics.title"]],
    ]);
  });

  it("never substitutes a Vendoo or reference form for a direct marketplace", () => {
    for (const tab of ["ebay", "poshmark", "mercari", "depop", "sellwild"]) {
      expect(fieldsForMarketplaceForm(listing, tab, undefined)).toEqual([]);
      expect(fieldsForMarketplaceForm(listing, tab, [{ marketplace: tab, known: true, category_id: "1", fields: [] }])).toEqual([]);
    }
  });

  it("shows the marketplace form's Price as this marketplace's own price, in its place", () => {
    const form: MarketplaceForm = { marketplace: "depop", native: true, known: true, category_id: "menswear_jeans", fields: [
      { key: "description", path: "depop_specifics.description", label: "Description", value: "", required: true, multi: false, selection_only: false, options: [] },
      { key: "price", path: "price", label: "Price", value: 20, required: true, multi: false, selection_only: false, options: [], input_type: "number" },
      { key: "brand", path: "depop_specifics.brand", label: "Brand", value: "", required: false, multi: false, selection_only: false, options: [] },
    ] };
    expect(fieldsForMarketplaceForm(listing, "depop", [form]).map((field) => [field.key, field.label.replace(/\s*\*$/, "")])).toEqual([
      ["depop_specifics.description", "Description"], ["depop_specifics.price", "Price"], ["depop_specifics.brand", "Brand"],
    ]);
  });

  it("fills a marketplace title from General and keeps an edit on that marketplace", () => {
    const form: MarketplaceForm = { marketplace: "ebay", native: true, known: true, category_id: "15687", fields: [
      { key: "title", path: "title", label: "Title", value: "Nike tee", required: true, multi: false, selection_only: false, options: [] },
      { key: "description", path: "description", label: "Description", value: "About it", required: true, multi: false, selection_only: false, options: [] },
      { key: "size", path: "ebay_specifics.Size", label: "Size", value: "L", required: true, multi: false, selection_only: true,
        options: ["L"], locked: true, help: "Size follows the tag size on General. Change it there." },
    ] };
    const fields = fieldsForMarketplaceForm(listing, "ebay", [form]);
    const title = fields.find((field) => field.key === "ebay_specifics.title");
    expect(title?.label.replace(/\s*\*$/, "")).toBe("Title");
    expect(title?.locked).toBeFalsy();
    expect(fields.find((field) => field.key === "ebay_specifics.description")?.type).toBe("textarea");
    expect(fields.find((field) => field.key === "ebay_specifics.Size")).toMatchObject({
      locked: false, help: "Filled from General. A change here stays on this marketplace.",
    });
    expect(fields.some((field) => field.key === "title")).toBe(false);
  });

  it("keeps General and the remaining marketplace editors available", () => {
    const general = fieldsForMarketplaceForm(listing, "general", undefined);
    expect(general.filter((field) => field.key === "title")).toHaveLength(1);
    expect(general.some((field) => field.key === "category_path")).toBe(true);
    const etsy = fieldsForMarketplaceForm({ ...listing, etsy_specifics: { whoMade: "Another company or person" } }, "etsy", undefined);
    expect(etsy.find((field) => field.key === "etsy_specifics.whoMade")?.label).toBe("Who Made It?");
    expect(etsy.filter((field) => field.key === "etsy_specifics.title")).toHaveLength(1);
    expect(etsy.find((field) => field.key === "etsy_specifics.description")).toBeTruthy();
    expect(etsy.find((field) => field.key === "etsy_specifics.brand")?.label.replace(/\s*\*$/, "")).toBe("Brand");
    expect(etsy.find((field) => field.key === "title")).toBeUndefined();
    expect(etsy.find((field) => field.key === "brand")).toBeUndefined();
    expect(etsy.find((field) => field.key === "condition")?.key).toBe("condition");
  });

  it("adds no measurement rows to Depop, whose sell page has none", () => {
    const measured = fieldsForMarketplaceForm({
      ...listing,
      depop_specifics: {
        measurements: { unit: "in", values: [{ id: "pit_to_pit", label: "Pit to pit", value: "22", unit: "in" }] },
      },
    }, "depop", undefined);
    expect(measured).toEqual([]);
  });

  it("fills bottoms sizes only on forms that are not the marketplace's own", () => {
    const row = (tab: string, key: string, label: string) => ({
      key: `${tab}_specifics.${key}`, path: `${tab}_specifics.${key}`, label, value: "", required: false,
      multi: false, selection_only: false, options: [],
    });
    const formFor = (tab: string, keys: [string, string][], native = false): MarketplaceForm => ({
      marketplace: tab, native, known: true, category_id: "jeans", fields: keys.map(([key, label]) => row(tab, key, label)),
    });
    const pants = { ...listing, title: "Anne Klein 8 Pants", department: "Women", size: "8", category_path: "Clothing > Women > Pants" };
    expect(fieldsForMarketplaceForm(pants, "etsy", [formFor("etsy", [["size", "Size"]])])
      .find((field) => field.key === "etsy_specifics.size")?.options).toEqual(expect.arrayContaining(["0", "8", "24"]));
    expect(fieldsForMarketplaceForm(pants, "vinted", [formFor("vinted", [["size", "Size"]])])
      .find((field) => field.key === "vinted_specifics.size")?.options)
      .toEqual(expect.arrayContaining(["S / 8", "XXL / 24", "XXS / 0"]));
    // A direct marketplace's own form is shown as it came: no reference choices, no extra rows.
    for (const tab of ["ebay", "poshmark", "mercari", "depop", "sellwild"]) {
      const fields = fieldsForMarketplaceForm(pants, tab, [formFor(tab, [["size", "Size"]], true)]);
      expect(fields.map((field) => [field.key, field.options])).toEqual([[`${tab}_specifics.size`, []]]);
    }
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
    }
  });
});
