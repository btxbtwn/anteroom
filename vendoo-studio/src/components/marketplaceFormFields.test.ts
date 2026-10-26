import { describe, expect, it } from "vitest";
import type { MarketplaceForm } from "../api/types";
import { formFieldText, getListingEditorValue } from "../listingPaths";
import { fieldsForMarketplaceForm, sellwildChoiceLabels, withDirectChoices } from "./ListingEditor";

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
  it("retains native multi-select rules when an existing listing row matches the schema", () => {
    const forms: MarketplaceForm[] = [{ ...schema[0], fields: [{
      key: "Season", label: "Season", value: "Spring, Summer", required: false,
      multi: true, selection_only: true, options: ["Spring", "Summer", "Fall", "Winter"],
      max_values: 4, max_length: 65, options_source: "marketplace",
    }] }];
    const fields = fieldsForMarketplaceForm({ ...listing, ebay_specifics: { season: ["Spring", "Summer"] } }, "ebay", forms);
    expect(fields.find((field) => field.label === "Season")).toMatchObject({
      multi: true, selectionOnly: true, maxValues: 4, maxLength: 65, optionsSource: "marketplace",
    });
  });

  it("shows the shared listing fields on a marketplace, then that marketplace's own", () => {
    const labels = fieldsForMarketplaceForm(listing, "mercari", undefined).map((field) => field.label);
    expect(labels.slice(0, 6)).toEqual(["Title", "Description", "Price", "Brand", "Condition", "Size"]);
    expect(labels).toContain("Package Weight (oz)");
    expect(labels.filter((label) => label === "Title")).toHaveLength(1);
  });

  it("offers Mercari's own conditions and no quantity, color or hand-typed shipping label", () => {
    const mercariSchema: MarketplaceForm[] = [{
      marketplace: "mercari", category_id: "1931", known: true,
      fields: [
        ...["Quantity", "Shipping Label", "Delivery Method", "Who will pay for delivery?", "Payer", "Who pays"].map((label) => ({
          key: label, label, value: "", required: false, multi: false, selection_only: false, options: [] as string[],
        })),
        { key: "size", label: "Size", value: "XL (18/20)", required: true, multi: false, selection_only: true,
          options: ["XL (14-16)", "2XL (18)"] },
      ],
    }];
    const fields = fieldsForMarketplaceForm({ ...listing, primaryColor: "Blue", quantity: 1,
      mercari_specifics: { ...listing.mercari_specifics, payer: "1" },
    }, "mercari", mercariSchema);
    const labels = fields.map((field) => field.label.replace(/\s*\*$/, ""));
    for (const absent of ["Quantity", "Primary Color", "Secondary Color", "Shipping Label", "Delivery Method", "Who will pay for delivery?", "Payer", "Who pays"]) {
      expect(labels).not.toContain(absent);
    }
    const condition = fields.find((field) => field.label === "Condition");
    expect(condition).toMatchObject({ key: "mercari_specifics.condition", options: ["New", "Like new", "Good", "Fair", "Poor"],
      defaultValue: "Good" });
    expect(fields.find((field) => field.label.startsWith("Size"))?.options).toEqual(["XL (14-16)", "2XL (18)"]);
  });

  it("shows the live eBay category beside the shared one", () => {
    const fields = fieldsForMarketplaceForm({
      ...listing,
      category_path: "Clothing > Shirts",
      direct_category_ids: { ebay: "15687", ebay_path: "Clothing, Shoes & Accessories > Men > Shirts" },
    }, "ebay", undefined);
    expect(fields.find((field) => field.label === "eBay category")).toMatchObject({ locked: true });
    expect(fields.filter((field) => field.key === "category_path")).toHaveLength(1);
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

  it("shows the eBay category's own aspects once its schema is known", () => {
    const labels = fieldsForMarketplaceForm(listing, "ebay", schema).map((field) => field.label);
    for (const fixed of ["Size Type", "Neckline", "Garment Care", "Strap Type"]) expect(labels).not.toContain(fixed);
    expect(labels).toContain("Primary Store Category");
    const fallback = fieldsForMarketplaceForm(listing, "ebay", undefined);
    expect(fallback.map((field) => field.label)).not.toContain("Size Type");
    expect(fallback.find((field) => field.label === "Neckline")?.key).toBe("ebay_specifics.neckline");
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

  it("stores Depop's type attribute as its type and hides size grouping for regular sizing", () => {
    const depopListing = {
      ...listing,
      sizeType: "Regular",
      depop_specifics: { type: "Windbreaker", sizeGrouping: "Does Not Apply" },
    };
    const option = (key: string, label: string, options: string[]) => ({
      key, label, value: "", required: false, multi: true, selection_only: true, options,
    });
    const depopSchema: MarketplaceForm[] = [{
      marketplace: "depop", category_id: "kidswear_jackets", known: true,
      fields: [option("attribute_jacket-type", "Type", ["Puffer", "Windbreaker"]),
        option("attribute_body-fit", "Size grouping", ["Petite", "Tall"])],
    }];
    const fields = fieldsForMarketplaceForm(depopListing, "depop", depopSchema);
    const type = fields.filter((field) => field.label === "Type");
    expect(type.map((field) => field.key)).toEqual(["depop_specifics.type"]);
    expect(type[0].options).toEqual(["Puffer", "Windbreaker"]);
    expect(fields.some((field) => field.key === "depop_specifics.sizeGrouping")).toBe(false);
    const petite = fieldsForMarketplaceForm({ ...depopListing, sizeType: "Petite" }, "depop", depopSchema);
    expect(petite.filter((field) => field.key === "depop_specifics.sizeGrouping")).toHaveLength(1);
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
  it("edits Poshmark's own condition, colors and sub-category, and hides Vendoo's delist message", () => {
    const posh = { ...listing, primaryColor: "Grey", secondaryColor: "Blue",
      poshmark_specifics: { delistingAttemptMessages: "Does Not Apply", styleTags: ["Casual"] } };
    const fields = fieldsForMarketplaceForm(posh, "poshmark", undefined);
    const byLabel = (label: string) => fields.filter((field) => field.label === label);
    expect(byLabel("Condition")).toHaveLength(1);
    expect(byLabel("Condition")[0]).toMatchObject({ key: "poshmark_specifics.poshmarkCondition", defaultValue: "Good" });
    expect(byLabel("Condition")[0].options).toContain("New With Tags (NWT)");
    expect(byLabel("Primary Color")[0]).toMatchObject({ key: "poshmark_specifics.poshmarkColor", defaultValue: "Gray" });
    expect(byLabel("Secondary Color")[0]).toMatchObject({ key: "poshmark_specifics.poshmarkSecondaryColor", defaultValue: "Blue" });
    expect(fields.map((field) => field.key)).not.toContain("poshmark_specifics.delistingAttemptMessages");
    const live = withDirectChoices(fields, { poshmark: { category: ["Kids", "Jackets & Coats"], subcategories: ["Puffers", "None"],
      colors: ["Blue", "Gray"], conditions: [] } });
    expect(live.find((field) => field.label === "Sub-category")?.options).toEqual(["Puffers", "None"]);
    expect(live.find((field) => field.label === "Primary Color")?.options).toEqual(["Blue", "Gray"]);
  });

  it("shows Sellwild's direct category and condition, not Vendoo's retired category or a brand", () => {
    const sellwild = { ...listing, category_path: "Clothing > Kids", sellwild_specifics: { categoryId: "183", category_path: ["Fashion", "Mens Clothing"] } };
    const fields = fieldsForMarketplaceForm(sellwild, "sellwild", schema.map((form) => ({ ...form, marketplace: "sellwild" })));
    const labels = fields.map((field) => field.label);
    expect(labels).not.toContain("Brand");
    expect(labels.filter((label) => label === "Category")).toHaveLength(1);
    expect(labels.filter((label) => label === "Condition")).toHaveLength(1);
    expect(fields.map((field) => field.key)).not.toContain("sellwild_specifics.category_path");
    const options = { categories: [{ id: 246, label: "Kids & Baby > Kids Clothing" }], conditions: [{ id: 3, label: "Good" }, { id: 2, label: "Gently Used" }],
      suggested: { category_id: 246, condition_id: 3 } };
    const shown = withDirectChoices(fields, { sellwild: sellwildChoiceLabels(options, { sellwild_condition_id: 2 } as never) });
    expect(shown.find((field) => field.key === "sellwild.category")).toMatchObject({ locked: true, defaultValue: "Kids & Baby > Kids Clothing" });
    expect(shown.find((field) => field.key === "sellwild.condition")?.defaultValue).toBe("Gently Used");
  });
});
