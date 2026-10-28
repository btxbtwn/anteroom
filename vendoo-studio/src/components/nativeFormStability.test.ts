import { describe, expect, it } from "vitest";
import {
  categorySpecificFieldsStale,
  listingCategorySignature,
  nativeFieldVisible,
  nativeFormCategoryKey,
  nativeFormNotes,
  nativeFormStructureKey,
  presentNativeField,
  resolvedEditorValue,
  type NativeFieldContext,
  type NativeFormRow,
} from "./nativeFormStability";

const depop = {
  price: 24,
  brand: "Vassarette",
  direct_category_ids: { depop: "womenswear_tops" },
  depop_specifics: { condition: "used_good", attributes: { material: ["cotton"] } },
};

describe("native form structure", () => {
  it("stays put when a value changes and moves when the category changes", () => {
    const priced = { ...depop, price: 40, brand: "Other" };
    expect(nativeFormStructureKey(priced, "depop")).toBe(nativeFormStructureKey(depop, "depop"));
    expect(nativeFormCategoryKey(priced, "depop")).toBe("womenswear_tops");
    expect(nativeFormStructureKey({ ...depop, direct_category_ids: { depop: "womenswear_dresses" } }, "depop"))
      .toBe("womenswear_dresses");
  });

  it("reloads eBay only when the category changes, including a category written onto the listing", () => {
    const listing = {
      direct_category_ids: { ebay: "15687" },
      ebay_specifics: { listingFormat: "FIXED_PRICE", auctionStartPrice: "8", variations: { rows: [] } },
    };
    const same = { ...listing, price: 12, ebay_specifics: { ...listing.ebay_specifics, auctionStartPrice: "9" } };
    expect(nativeFormStructureKey(same, "ebay")).toBe("15687");
    expect(nativeFormStructureKey({
      ...listing,
      ebay_specifics: { ...listing.ebay_specifics, listingFormat: "AUCTION" },
    }, "ebay")).toBe(nativeFormStructureKey(listing, "ebay"));
    expect(nativeFormStructureKey({
      ...listing,
      ebay_specifics: { ...listing.ebay_specifics, variations: { rows: [{ values: { Size: "M" } }] } },
    }, "ebay")).toBe(nativeFormStructureKey(listing, "ebay"));
    expect(nativeFormStructureKey({ ebay_specifics: { categoryId: "15709" } }, "ebay")).toBe("15709");
  });

  it("reloads Mercari for a new category or package, as its label picker does", () => {
    const listing = {
      direct_category_ids: { mercari: "1911" },
      weight_oz: 8,
      mercari_specifics: { shippingMethod: "MERCARI", brand: "Nike" },
    };
    const key = nativeFormStructureKey(listing, "mercari");
    expect(nativeFormStructureKey({ ...listing, mercari_specifics: { ...listing.mercari_specifics, brand: "Gap" } }, "mercari"))
      .toBe(key);
    expect(nativeFormStructureKey({ ...listing, price: 40 }, "mercari")).toBe(key);
    expect(nativeFormStructureKey({ ...listing, direct_category_ids: { mercari: "2008" } }, "mercari")).not.toBe(key);
    // The labels offered depend on the method, the weight and the box.
    expect(nativeFormStructureKey({ ...listing, mercari_specifics: { shippingMethod: "SOYO" } }, "mercari")).not.toBe(key);
    expect(nativeFormStructureKey({ ...listing, weight_oz: 16 }, "mercari")).not.toBe(key);
    expect(nativeFormStructureKey({ ...listing, package_dimensions_in: "10x8x4" }, "mercari")).not.toBe(key);
    expect(nativeFormStructureKey({ ...listing, mercari_specifics: { ...listing.mercari_specifics,
      package: { fitsShoebox: "yes" } } }, "mercari")).not.toBe(key);
  });

  it("asks Mercari's package questions with Prepaid label, and the sides only when it does not fit a shoebox", () => {
    const row = (key: string, defaultValue = "") => ({ key, label: key, native: true, defaultValue });
    const fields = [row("mercari_specifics.shippingMethod", "MERCARI"), row("mercari_specifics.package.pounds", "1"),
      row("mercari_specifics.package.fitsShoebox"), row("mercari_specifics.package.length", "16"),
      row("mercari_specifics.shippingClassId")];
    const shown = (local: Record<string, string>) => fields
      .filter((field) => nativeFieldVisible(field, { tab: "mercari", listing: {}, local, fields, categoryStale: false }))
      .map((field) => field.key.split(".").pop());
    expect(shown({})).toEqual(["shippingMethod", "pounds", "fitsShoebox", "length", "shippingClassId"]);
    expect(shown({ "mercari_specifics.package.fitsShoebox": "yes" })).toEqual(["shippingMethod", "pounds", "fitsShoebox", "shippingClassId"]);
    expect(shown({ "mercari_specifics.shippingMethod": "SOYO" })).toEqual(["shippingMethod"]);
  });

  it("treats a category change as a new form and a price edit as the same form", () => {
    expect(listingCategorySignature({ ...depop, price: 40 })).toBe(listingCategorySignature(depop));
    expect(listingCategorySignature({ ...depop, direct_category_ids: { depop: "womenswear_dresses" } }))
      .not.toBe(listingCategorySignature(depop));
  });

  it("treats the on-screen form as the previous category only while its replacement is loading", () => {
    expect(categorySpecificFieldsStale("womenswear_tops", "womenswear_dresses", true)).toBe(true);
    expect(categorySpecificFieldsStale("womenswear_tops", "womenswear_tops", true)).toBe(false);
    expect(categorySpecificFieldsStale("womenswear_tops", "womenswear_dresses", false)).toBe(false);
  });
});

describe("native form values", () => {
  it("shows the saved value instead of the form's stale copy", () => {
    expect(resolvedEditorValue(
      { key: "price", defaultValue: 24 },
      { price: 40 },
    )).toBe(40);
    expect(resolvedEditorValue(
      { key: "depop_specifics.condition", selectionOnly: true, options: ["Good"], optionValues: { Good: "used_good" }, defaultValue: "used_fair" },
      { depop_specifics: { condition: "used_good" } },
    )).toBe("used_good");
  });

  it("shows an eBay category as its breadcrumb, not its numeric id", () => {
    expect(resolvedEditorValue(
      { key: "direct_category_ids.ebay", defaultValue: "Clothing › Women › Tops" },
      { direct_category_ids: { ebay: "15687", ebay_path: "Clothing › Women › Tops" } },
    )).toBe("Clothing › Women › Tops");
    expect(resolvedEditorValue(
      { key: "direct_category_ids.ebay", defaultValue: "Clothing › Women › Tops" },
      { direct_category_ids: { ebay: "15687" } },
    )).toBe("Clothing › Women › Tops");
  });

  it("keeps a locked computed value and a resolved choice the listing has not stored as an id", () => {
    expect(resolvedEditorValue(
      { key: "sku", locked: true, defaultValue: "" },
      { sku: "ar-abc123" },
    )).toBe("");
    expect(resolvedEditorValue(
      { key: "ebay_specifics.Condition", selectionOnly: true, options: ["Pre-owned"], optionValues: { "Pre-owned": "3000" }, defaultValue: "3000" },
      { ebay_specifics: { Condition: "Good" } },
    )).toBe("3000");
  });

  it("shows Sellwild's weight and sides only for the packages its page asks them for", () => {
    const keys = ["weight", "height", "width", "depth"].map((side) => `sellwild_specifics.package.${side}`);
    const fields = [
      { key: "sellwild_specifics.parcelTemplate", optionValues: {
        "Custom Package Dimensions": "", "Small Flat Rate Box": "USPS_SmallFlatRateBox", "Soft Pack (poly mailer)": "USPS_SoftPack",
      } },
      ...keys.map((key) => ({ key, locked: true })),
    ];
    const shown = (pick: string) => keys.filter((key) => nativeFieldVisible({ key }, {
      tab: "sellwild", listing: {}, local: { "sellwild_specifics.parcelTemplate": pick }, fields, categoryStale: false,
    }));
    expect(shown("Custom Package Dimensions")).toEqual(keys);
    expect(shown("Soft Pack (poly mailer)")).toEqual(["sellwild_specifics.package.weight"]);
    expect(shown("Small Flat Rate Box")).toEqual([]);
  });

  it("hides the previous category's rows, and reveals a toggle's row without reloading", () => {
    const fields = [
      { key: "price" },
      { key: "depop_specifics.attributes.material", categorySpecific: true },
      { key: "depop_specifics.worldwideShipping.enabled" },
      { key: "depop_specifics.worldwideShipping.price" },
    ];
    const context = { tab: "depop", listing: depop, local: {}, fields, categoryStale: true };
    expect(fields.filter((field) => nativeFieldVisible(field, context)).map((field) => field.key))
      .toEqual(["price", "depop_specifics.worldwideShipping.enabled"]);
    expect(nativeFieldVisible(fields[3], {
      ...context,
      categoryStale: false,
      local: { "depop_specifics.worldwideShipping.enabled": "true" },
    })).toBe(true);
    expect(nativeFieldVisible(
      { key: "mercari_specifics.shippingClassId" },
      {
        tab: "mercari",
        listing: {},
        local: { "mercari_specifics.shippingMethod": "Ship on your own" },
        fields: [{ key: "mercari_specifics.shippingMethod", optionValues: { "Ship on your own": "SOYO" } }],
        categoryStale: false,
      },
    )).toBe(false);
  });

  it("shows eBay auction rows only for an auction, and offers only for a single fixed-price listing", () => {
    const fields: NativeFormRow[] = [
      { key: "ebay_specifics.listingFormat", options: ["Buy It Now", "Auction"], optionValues: { "Buy It Now": "FIXED_PRICE", Auction: "AUCTION" } },
      { key: "ebay_specifics.listingDuration", options: ["Good 'Til Cancelled", "7 days"], optionValues: { "Good 'Til Cancelled": "GTC", "7 days": "DAYS_7" }, defaultValue: "GTC" },
      { key: "ebay_specifics.auctionStartPrice" },
      { key: "ebay_specifics.price" },
      { key: "ebay_specifics.bestOffer.enabled" },
      { key: "ebay_specifics.variations" },
      { key: "quantity" },
    ];
    const context: NativeFieldContext = {
      tab: "ebay",
      listing: { ebay_specifics: { listingFormat: "FIXED_PRICE", variations: { rows: [] } } },
      local: {},
      fields,
      categoryStale: false,
    };
    const visible = (extra: Partial<NativeFieldContext>) => fields
      .filter((field) => nativeFieldVisible(field, { ...context, ...extra }))
      .map((field) => field.key);
    // eBay's page: Buy It Now has an item price and no duration; an auction has a duration and bids, no item price.
    expect(visible({})).toEqual([
      "ebay_specifics.listingFormat", "ebay_specifics.price", "ebay_specifics.bestOffer.enabled", "ebay_specifics.variations", "quantity",
    ]);
    expect(visible({ local: { "ebay_specifics.listingFormat": "Auction" } })).toEqual([
      "ebay_specifics.listingFormat", "ebay_specifics.listingDuration", "ebay_specifics.auctionStartPrice", "quantity",
    ]);
    const varied = {
      ...context,
      listing: { ebay_specifics: { listingFormat: "FIXED_PRICE", variations: { rows: [{ values: { Size: "M" } }] } } },
    };
    expect(visible(varied)).not.toContain("ebay_specifics.bestOffer.enabled");
    expect(visible(varied)).toContain("ebay_specifics.variations");
    const duration = presentNativeField(fields[1], { ...context, local: { "ebay_specifics.listingFormat": "Auction" } });
    expect(duration.locked).toBe(false);
    expect(duration.options).toEqual(["7 days"]);
    expect(presentNativeField(fields[1], context).locked).toBe(true);
    expect(presentNativeField(fields[0], varied).options).toEqual(["Buy It Now"]);
    expect(presentNativeField(fields.find((field) => field.key === "quantity")!, varied).locked).toBe(true);
    expect(nativeFormNotes("ebay", varied.listing)).toEqual([
      "With variations, Price is the price of any variation left without its own. eBay takes no offers on a listing with variations.",
    ]);
  });

  it("shows the listing start time only while scheduling is on, and the offer amounts only while offers are on", () => {
    const fields: NativeFormRow[] = [
      { key: "ebay_specifics.schedule.enabled", defaultValue: false },
      { key: "ebay_specifics.schedule.startAt" },
      { key: "ebay_specifics.bestOffer.enabled", defaultValue: true },
      { key: "ebay_specifics.bestOffer.minimumOfferPrice" },
      { key: "ebay_specifics.bestOffer.autoAcceptPrice" },
    ];
    const context: NativeFieldContext = { tab: "ebay", listing: {}, local: {}, fields, categoryStale: false };
    const visible = (local: Record<string, string>) => fields
      .filter((field) => nativeFieldVisible(field, { ...context, local }))
      .map((field) => field.key);
    expect(visible({})).toEqual([
      "ebay_specifics.schedule.enabled", "ebay_specifics.bestOffer.enabled",
      "ebay_specifics.bestOffer.minimumOfferPrice", "ebay_specifics.bestOffer.autoAcceptPrice",
    ]);
    expect(visible({ "ebay_specifics.schedule.enabled": "true" })).toContain("ebay_specifics.schedule.startAt");
    expect(visible({ "ebay_specifics.bestOffer.enabled": "false" })).toEqual([
      "ebay_specifics.schedule.enabled", "ebay_specifics.bestOffer.enabled",
    ]);
  });

  it("hides Mercari's free-shipping choice under ship on your own, and the minimum price until Smart pricing is on", () => {
    const fields: NativeFormRow[] = [
      { key: "mercari_specifics.shippingMethod", options: ["Prepaid label", "Ship on your own"], optionValues: { "Prepaid label": "MERCARI", "Ship on your own": "SOYO" } },
      { key: "mercari_specifics.shippingPayerId", options: ["Yes (Recommended)", "No"], optionValues: { "Yes (Recommended)": "2", No: "1" }, defaultValue: "1" },
      { key: "mercari_specifics.smartPricing.enabled", defaultValue: false },
      { key: "mercari_specifics.smartPricing.minimumPrice" },
    ];
    const context = { tab: "mercari", listing: {}, local: {}, fields, categoryStale: false };
    expect(nativeFieldVisible(fields[1], context)).toBe(true);
    expect(nativeFieldVisible(fields[1], { ...context, local: { "mercari_specifics.shippingMethod": "Ship on your own" } })).toBe(false);
    expect(nativeFieldVisible(fields[3], context)).toBe(false);
    expect(nativeFieldVisible(fields[3], { ...context, local: { "mercari_specifics.smartPricing.enabled": "true" } })).toBe(true);
  });

  it("hides Poshmark's Lowest Price until Automate Offers is on", () => {
    const fields: NativeFormRow[] = [
      { key: "poshmark_specifics.smartSell.enabled", defaultValue: false },
      { key: "poshmark_specifics.smartSell.minimumPrice" },
    ];
    const context = { tab: "poshmark", listing: {}, local: {}, fields, categoryStale: false };
    expect(nativeFieldVisible(fields[1], context)).toBe(false);
    expect(nativeFieldVisible(fields[1], { ...context, local: { "poshmark_specifics.smartSell.enabled": "true" } })).toBe(true);
  });
});
